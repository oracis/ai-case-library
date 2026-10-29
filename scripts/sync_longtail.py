#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_longtail.py — 长尾平台草稿同步（CSDN 同步助手 MCP 封装）
================================================================

把 drafts/<src>/<case>.md 一键同步到腾讯云/阿里云等长尾平台草稿箱，
自动带上 MCP `sync_article` 支持的全部字段：

  · title / markdown   —— 草稿正文（剥「> 平台：」水印行）
  · cover              —— 自动找封面：out/toutiao/<case>/cover.png →
                          out/agnes/<case>.png → --cover 指定，转 data URI
  · category / tags    —— --category / --tags（默认 人工智能）
  · idempotencyKey     —— 自动生成 <case>-<date>-<序号>，写入台账防重发

⚠ MCP 覆盖不了的（要人工在编辑器补）：
  · 阿里云「摘要」——sync_article 无此字段；编辑器有「AI 生成摘要」按钮可一键
  · 「发布至社区」若 category 未被平台映射成功，发表前手动核对
  · upload_image_file 图床上传需要目标平台页面上下文（CSRF），故走 data URI 直嵌

依赖：`scripts/_probe_csdn_sync.py` 的 MCP 客户端类（stdio JSON-RPC）。
前置：浏览器开着、装了 CSDN 同步助手扩展且 MCP 开关打开、当前标签页是普通网页。

用法：
    python -X utf8 scripts/sync_longtail.py --case upvoty
    python -X utf8 scripts/sync_longtail.py --case upvoty --platforms aliyun tencentcloud
    python -X utf8 scripts/sync_longtail.py --case upvoty --cover out/agnes/x.png --category 人工智能
    python -X utf8 scripts/sync_longtail.py --task upvoty-2026-10-28-1   # 只查任务
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from _probe_csdn_sync import MCP          # noqa: E402
from csdn_publish import split_md         # noqa: E402

LEDGER = os.path.join(ROOT, "data", "longtail_sync.json")
# 长尾池默认平台：两朵云（有 finalize 收尾）+ 思否（2026-10-28 用户定案先进
# 长尾池，id 实测 = segmentfault）。未登录平台自动软降级踢出本批，不拖死整批。
DEFAULT_PLATFORMS = ["aliyun", "tencentcloud", "segmentfault"]
DEFAULT_TAGS = ["人工智能", "AI产品", "独立开发"]


def find_cover(case, explicit=None):
    """按优先级找封面文件，返回 (path, bytes) 或 (None, None)。"""
    cands = []
    if explicit:
        cands.append(explicit)
    cands += [
        os.path.join(ROOT, "out", "toutiao", case, "cover.png"),
        os.path.join(ROOT, "out", "agnes", "%s.png" % case),
    ]
    for p in cands:
        if p and os.path.exists(p):
            return p, open(p, "rb").read()
    return None, None


def oss_put_cover(case, cover_path, cover_bytes):
    """封面传 OSS（复用 deploy_oss 的零依赖客户端），返回公网 URL。

    cover 参数已被实测证明会被腾讯云适配器丢弃（2026-10-28，337075 弹窗空），
    所以改走「正文首图」注入——需要 http(s) 外链，data URI 可能被清洗。
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import deploy_oss

    # 凭据：env → .env 文件 → HKCU 注册表（本机 Git Bash 不继承用户级 env 的坑）
    envs = {}
    env_file = os.path.join(ROOT, ".env")
    if os.path.exists(env_file):
        envs = deploy_oss.load_env_file(env_file)
    if not os.environ.get("OSS_BUCKET") and envs.get("OSS_BUCKET"):
        os.environ["OSS_BUCKET"] = envs["OSS_BUCKET"]
    if not os.environ.get("OSS_REGION") and envs.get("OSS_REGION"):
        os.environ["OSS_REGION"] = envs["OSS_REGION"]
    if not os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID"):
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
            for name in ("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET"):
                try:
                    val, _ = winreg.QueryValueEx(k, name)
                    os.environ.setdefault(name, val)
                except OSError:
                    pass
        except OSError:
            pass
    ak, sk = deploy_oss.resolve_credentials()

    bucket = os.environ.get("OSS_BUCKET") or envs.get("OSS_BUCKET", "")
    region = os.environ.get("OSS_REGION") or envs.get("OSS_REGION", "")
    if not (ak and sk and bucket and region):
        raise SystemExit("OSS 凭据不全（ak=%s bucket=%s region=%s），无法托管封面"
                         % (bool(ak), bucket, region))

    ext = os.path.splitext(cover_path)[1] or ".png"
    key = "covers/%s%s" % (case, ext)
    oss = deploy_oss.OSS(ak, sk, region)
    st, _, _ = oss.request("PUT", bucket=bucket, key=key, body=cover_bytes,
                           headers={"Content-Type": "image/png" if ext == ".png" else "image/jpeg"})
    if st not in (200, 201):
        raise SystemExit("OSS 上传失败 HTTP %s" % st)
    base = ""
    try:
        with open(os.path.join(ROOT, "data", "site.json"), encoding="utf-8") as f:
            base = (json.load(f).get("url") or "").rstrip("/")
    except Exception:                                       # noqa: BLE001
        pass
    if base:
        url = "%s/%s" % (base, key)
    else:
        url = "https://%s.oss-%s.aliyuncs.com/%s" % (bucket, region, key)
    return url


def load_ledger():
    if os.path.exists(LEDGER):
        with open(LEDGER, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def remember(key, entry):
    ledger = load_ledger()
    ledger[key] = entry
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, LEDGER)


def next_key(case):
    """<case>-<date>-<序号>：台账里同日已有的序号 +1，保证幂等键全新。"""
    ledger = load_ledger()
    day = time.strftime("%Y-%m-%d")
    n = 1
    while "%s-%s-%d" % (case, day, n) in ledger:
        n += 1
    return "%s-%s-%d" % (case, day, n)


def read_draft(path):
    with open(path, "r", encoding="utf-8") as f:
        md = f.read()
    # 剥草稿管线元信息水印（平台名在长尾平台上是错的）
    md = "\n".join(ln for ln in md.split("\n") if not ln.startswith("> 平台："))
    return split_md(md)


def wait_ext(m, timeout_s=90):
    """扩展是 WS client，server 每次新起进程它要重连；轮询直到能用。

    list_platforms 的返回形状不稳定（有时是数组，有时包一层 dict / 双重编码），
    逐层解包直到拿到 list[dict]。"""
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            r = m.tools_call("list_platforms", {"forceRefresh": True})
            data = r["content"][0]["text"]
            for _ in range(3):
                if isinstance(data, str):
                    data = json.loads(data)
            if isinstance(data, dict):
                for v in ("platforms", "data", "list", "result", "items"):
                    if isinstance(data.get(v), list):
                        data = data[v]
                        break
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data
            last = "形状异常: %s" % str(data)[:120]
        except Exception as e:
            last = str(e)[:120]
        time.sleep(8)
    raise SystemExit("扩展一直没连上（%s）。检查：扩展 MCP 开关开着、浏览器开着、"
                     "当前标签页是普通网页。" % last)


def poll_task(m, key, tries=10, gap=8):
    for i in range(tries):
        try:
            r = m.tools_call("get_sync_task", {"idempotencyKey": key})
            t = json.loads(r["content"][0]["text"])
        except Exception as e:
            print("  [poll %d] 异常: %s" % (i, str(e)[:100]))
            time.sleep(gap)
            continue
        st = t.get("status")
        print("  [poll %d] status=%s" % (i, st), flush=True)
        if st not in ("preparing", "running", "pending"):
            return t
        time.sleep(gap)
    return None


def main():
    ap = argparse.ArgumentParser(description="长尾平台草稿同步（自动带封面/分类/标签）")
    ap.add_argument("--case", help="案例 id（对应 drafts/wechat/<case>.md）")
    ap.add_argument("--file", help="或直接指定 markdown 文件")
    ap.add_argument("--platforms", nargs="+", default=DEFAULT_PLATFORMS)
    ap.add_argument("--tags", nargs="+", default=DEFAULT_TAGS)
    ap.add_argument("--category", default="人工智能")
    ap.add_argument("--cover", default="", help="封面文件路径（缺省自动找）")
    ap.add_argument("--cover-mode", default="body", choices=["body", "param", "both", "none"],
                    help="封面策略：body=OSS托管+正文首图注入（默认）；param=cover参数（腾讯云会丢弃）；both；none")
    ap.add_argument("--task", help="只查任务结果（传幂等键）")
    ap.add_argument("--finalize", action="store_true",
                    help="收尾模式：不同步新稿，对已同步草稿补摘要/封面"
                         "（腾讯云 UI 一键提取+正文图选封面；阿里云 putDraft 直写）")
    ap.add_argument("--tc-draft", default="", help="腾讯云 draftId")
    ap.add_argument("--al-draft", default="", help="阿里云 draftId(aid)")
    ap.add_argument("--abstract", default="", help="阿里云摘要（缺省取正文首段）")
    ap.add_argument("--cover-url", default="",
                    help="阿里云封面外链（缺省用 OSS 托管的 covers/<case>.png）")
    ap.add_argument("--community", default="",
                    help="阿里云发布子社区（finalize 阶段 UI 选中+台账留档）")
    ap.add_argument("--publish", action="store_true",
                    help="发表阿里云草稿。必须同时给 --i-said-publish i-said-publish")
    ap.add_argument("--i-said-publish", dest="i_said_publish", default="")
    ap.add_argument("--headed", action="store_true",
                    help="finalize/publish 用有头 Chrome（默认无头）")
    args = ap.parse_args()

    if args.finalize:
        import yun_finalize as F
        cover_url = args.cover_url
        if al_draft := args.al_draft:
            if not cover_url:
                cp, cb = find_cover(args.case, "")
                if cb:
                    cover_url = oss_put_cover(args.case, cp, cb)
        F.run(case=args.case, file=args.file, tc_draft=args.tc_draft or None,
              al_draft=al_draft or None, abstract=args.abstract or None,
              cover_url=cover_url, community=args.community or None,
              headed=args.headed)
        return

    if args.publish:
        if args.i_said_publish != "i-said-publish":
            raise SystemExit("🚫 拒绝发表：--publish 必须与 --i-said-publish "
                             "i-said-publish 成对（用户明确说「发布」才允许）")
        if not args.al_draft:
            raise SystemExit("🚫 拒绝发表：--publish 需要 --al-draft")
        import yun_finalize as F
        proc, cdp = F._connect(args.headed)
        try:
            F.publish_aliyun(cdp, int(args.al_draft), args.community or None,
                         args.cover or "")
        finally:
            try:
                cdp.bws.close()
            except Exception:                                  # noqa: BLE001
                pass
        return

    if args.task:
        m = MCP()
        try:
            m.initialize()
            time.sleep(15)
            t = poll_task(m, args.task, tries=6)
            print(json.dumps(t, ensure_ascii=False, indent=1) if t else "任务仍在跑或查询失败")
        finally:
            m.close()
        return

    src = args.file or os.path.join(ROOT, "drafts", "wechat", "%s.md" % args.case)
    if not os.path.exists(src):
        raise SystemExit("草稿不存在: %s" % src)
    case = args.case or os.path.splitext(os.path.basename(src))[0]
    title, body = read_draft(src)
    import re
    if re.search(r"\d", title):
        print("⚠ 标题含数字（「标题禁金额」铁律），请先核：%s" % title)

    cover_path, cover_bytes = find_cover(case, args.cover)
    cover_url = ""
    if cover_bytes:
        if args.cover_mode in ("body", "both", "param"):
            if args.cover_mode == "param":
                cover_url = "data:image/png;base64," + base64.b64encode(cover_bytes).decode()
            else:
                cover_url = oss_put_cover(case, cover_path, cover_bytes)
                print("封面已托管: %s" % cover_url)
        if args.cover_mode in ("body", "both"):
            # 正文首图注入：腾讯云发布弹窗「正文图」区会自动抓正文内图片，
            # 添加封面可直接选它（2026-10-28 实测 cover 参数会被腾讯云适配器丢弃）
            body = "![](%s)\n\n%s" % (cover_url, body)
            print("已注入正文首图（cover-mode=%s）" % args.cover_mode)
    else:
        print("⚠ 没找到封面（out/toutiao/%s/cover.png 或 out/agnes/%s.png），"
              "不带封面同步（发表前可在编辑器补）" % (case, case))

    key = next_key(case)
    print("幂等键: %s" % key)

    m = MCP()
    try:
        m.initialize()
        platforms = wait_ext(m)
        authed = {p["id"] for p in platforms if p.get("authenticated")}
        missing = [x for x in args.platforms if x not in authed]
        if missing:
            # 软降级：缺登录的平台踢出本批并记进台账 note（思否等长尾平台
            # 登录时效各不相同，不该拖死整批）。全都没登录才硬退。
            if len(missing) == len(args.platforms):
                raise SystemExit("请求的平台全部未登录: %s（先登录再跑）" % missing)
            print("⚠ 平台未登录，本批跳过: %s（补登录后下批自动带上）" % missing)
        args.platforms = [x for x in args.platforms if x in authed]
        print("平台登录态 OK:", args.platforms)

        payload = {
            "idempotencyKey": key,
            "platforms": args.platforms,
            "title": title,
            "markdown": body,
            "tags": args.tags,
        }
        if args.category:
            payload["category"] = args.category
        if cover_url and args.cover_mode in ("param", "both"):
            # 实测腾讯云会丢弃 cover 参数；阿里云未验证——默认不传，走正文首图
            payload["cover"] = cover_url

        m.tools_call("sync_article", payload)
        print("已提交，轮询任务…", flush=True)
        time.sleep(12)
        t = poll_task(m, key)
    finally:
        try:
            m.close()
        except Exception:
            pass

    ok = {}
    if t:
        for r in t.get("results") or []:
            ok[r["platform"]] = {"ok": r.get("success"), "draftId": r.get("draftId"),
                                 "url": r.get("articleUrl")}
        remember(key, {
            "case": case, "title": title, "platforms": ok,
            "category": args.category, "cover": cover_path or "",
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        print("✓ 台账: %s ← %s" % (os.path.relpath(LEDGER, ROOT), key))
    else:
        # 任务没结束也先落账（响应丢失可 --task 复查，禁止换键重发）
        remember(key, {"case": case, "title": title, "platforms": "(未完成,用--task复查)",
                       "category": args.category, "cover": cover_path or "",
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        print("任务未在超时内完成：稍后 python scripts/sync_longtail.py --task %s 复查"
              % key)

    for pid, info in ok.items():
        mark = "✓" if info.get("ok") else "✗"
        print("%s %s draftId=%s %s" % (mark, pid, info.get("draftId"), info.get("url", "")))
    print("\n⚠ 人工收尾（MCP 不支持的字段）→ 用 --finalize 自动补：")
    print("  python scripts/sync_longtail.py --finalize --case %s "
          "--tc-draft <id> --al-draft <aid> [--community 云原生]" % case)
    print("  · 腾讯云：封面（正文图选）+ 摘要（一键提取）全自动")
    print("  · 阿里云：摘要 + coverUrl 直写；社区 UI 选中 + 台账留档")
    print("  · 发表（用户明确说「发布」才执行）：--publish --al-draft <aid> "
          "--i-said-publish i-said-publish")


if __name__ == "__main__":
    main()
