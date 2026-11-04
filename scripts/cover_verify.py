#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面回读 —— 回答「这个 case 在这个平台，封面到底设上了没有」。

为什么要有它（2026-11-05）
--------------------------
`publish_guard.check_cover()` 只保证「本地有 PNG + 命令带 --cover」，
PLAYBOOK 里那句 `⛔ 本地有 PNG ≠ 平台侧封面已设置` 一直只有注脚、没有判据。
2026-11-04 CSDN 三篇草稿「本地有图、平台侧 coverImage 全空」就是这条洞
第一次付账 —— 当时靠人肉打开后台才看出来。

本模块把「有没有封面」从**人的眼睛**挪到**可执行的判据**上。

五种状态，一种都不许省
----------------------
| state       | 含义                                   | 发布闸 |
|-------------|----------------------------------------|--------|
| `ok`        | 真去平台读了，字段非空，拿到 URL        | 放行   |
| `empty`     | 真去读了，字段是空的                    | **拦** |
| `unknown`   | 没读（没跑 --live / 台账没记录 / 过期） | warn   |
| `local`     | 封面是发布产物本身或随请求一次提交，平台侧没有可回读的独立字段 | warn |
| `none`      | 该平台脚本根本不上封面                  | warn   |

**`unknown` 与 `ok` 必须分开** —— 这是本模块存在的唯一理由。
把「没查过」写成「通过」是本仓库最常见的一类假绿
（`PLAYBOOK-quality` 假绿 1–13 里至少四条是这个形状）。
所以 `verify()` 在「读不到」时一律返回 `unknown`，绝不返回 `ok`。

平台能力表是按**代码现状**逐条核过的，不是照文档抄的
----------------------------------------------------
* 腾讯云：`yun_finalize.publish_tencent()` 发表后用 `verify_cover_online()`
  读线上卡片缩略图（CSS `background-image`），真判据，已在跑。
* 阿里云：`finalize_aliyun()` 会复核 `draftDetail.coverUrl`，但原来**只打印
  不判定** —— 2026-11-05 补成写台账（假绿形状：结果打印了，没人读）。
* CSDN：`csdn_list_drafts.list_status()` 的 `cover` 字段（走页面请求，
  不是那个至今没打通的签名 GET）。
* B站：草稿接口 `origin_image_urls` 优先、`image_urls` 兜底。
  ⛔ **不是 `banner_url`** —— 这个字段在草稿接口里恒为 `""`，
  拿它判「有没有封面」会永远误判成没有（`bilibili_publish._cover_of` 有定论）。
* 公众号：`wechat_publish.SELECTORS["cover_input"] is None`，
  「个人号走默认首图，暂不自动设」⇒ 脚本压根不上封面。
* 头条 / 小红书：题图随发布请求一次提交 / 首图即封面，平台侧没有
  独立封面字段可回读。
* 博客园 / 知乎：两个脚本里 `cover` / `封面` 一个都不出现。

用法::

    python -X utf8 scripts/cover_verify.py --case chessigma --platform tencentcloud
    python -X utf8 scripts/cover_verify.py --case chessigma --platform csdn --live
    python -X utf8 scripts/cover_verify.py --report          # 台账全览
    python -X utf8 scripts/cover_verify.py --matrix          # 九平台能力矩阵
"""

import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

DATA = os.path.join(ROOT, "data")
LEDGER = os.path.join(DATA, "cover_readback.json")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATES = ("ok", "empty", "unknown", "local", "none")

#: 台账记录的有效期（天）。超过就降级成 `unknown` ——
#: 一条三个月前的 `ok` 不能替今天作证（文章可能被改过、封面可能被平台清掉）。
TTL_DAYS = 30

#: 平台能力表。`probe` 说明「怎么读」：
#:   `live`         = 本模块能现场去读（`_PROBE` 里有实现）
#:   `publish-time` = 发表那一刻由发布脚本自己判，判完写台账
#:   None           = 没有可读的东西，只能靠人眼
CAP = {
    "wechat": {
        "mode": "none", "probe": None, "field": "",
        "why": "wechat_publish.SELECTORS['cover_input'] 为 None，脚本不上封面"
               "（个人号走默认首图）⇒ 群发前必须在后台手动选一张首图",
    },
    "toutiao": {
        "mode": "local", "probe": None, "field": "",
        "why": "题图随发布请求一次提交；草稿/文章接口没有可回读的独立封面字段",
    },
    "xhs": {
        "mode": "local", "probe": None, "field": "",
        "why": "首图即封面：笔记首图就是发布产物本身，不存在单独的封面字段",
    },
    "bilibili": {
        "mode": "live", "probe": "live",
        "field": "origin_image_urls[0] / image_urls[0]",
        "why": "草稿接口 origin_image_urls 优先、image_urls 兜底；"
               "⛔ banner_url 在该接口里恒为 \"\"，拿它判会永远误判成没封面",
    },
    "cnblogs": {
        "mode": "none", "probe": None, "field": "",
        "why": "cnblogs_publish.py 里没有封面逻辑（metaweblog 发布也不返回封面字段）",
    },
    "zhihu": {
        "mode": "none", "probe": None, "field": "",
        "why": "zhihu_publish.py 里没有封面逻辑；只到草稿，封面在编辑器里需人工",
    },
    "csdn": {
        "mode": "live", "probe": "live", "field": "coverImage[0]",
        "why": "csdn_list_drafts.list_status() 的 cover 字段"
               "（点 tab 抓页面自己的请求，不是至今没打通的签名 GET）",
    },
    "tencentcloud": {
        "mode": "live", "probe": "publish-time",
        "field": "线上卡片缩略图 background-image（og:image 是平台默认分享图，不能用）",
        "why": "yun_finalize.publish_tencent() 发表后调 verify_cover_online() 真读，"
               "结果写本台账",
    },
    "aliyun": {
        "mode": "live", "probe": "publish-time", "field": "draftDetail.coverUrl",
        "why": "finalize_aliyun() 的 putDraft 复核里有 coverUrl，原实现只打印不判定，"
               "2026-11-05 补成写台账",
    },
}

PLATFORMS = tuple(CAP)


# ---------------------------------------------------------------- 台账

def load_ledger(path=None):
    """读封面回读台账 `{case: {platform: {state, evidence, at, source}}}`。"""
    p = path or LEDGER
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def record(case, platform, state, evidence="", source="", path=None):
    """写一条回读结果。`state` 必须是 STATES 里的值。

    🚨 **只有真读过才许写 `ok`**。写 `ok` 的调用方必须能说出「读了哪个字段、
    读到什么值」——`evidence` 为空时本函数直接把 `ok` 降级成 `unknown`，
    这是对「结果打印了但没人读」那类假绿的最后一道闸。
    """
    if state not in STATES:
        raise ValueError("未知封面状态：%r" % (state,))
    if state == "ok" and not str(evidence or "").strip():
        state, evidence = "unknown", "调用方声称 ok 但没给证据 ⇒ 降级为未回读"
    p = path or LEDGER
    d = load_ledger(p)
    slot = d.setdefault(case, {})
    slot[platform] = {
        "state": state,
        "evidence": str(evidence or "")[:300],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source or "manual",
    }
    _save(d, p)
    return slot[platform]


def _save(d, path):
    import atomicio
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomicio.write_json(path, d, indent=1, sort_keys=True)


def get_record(case, platform, path=None):
    return (load_ledger(path).get(case) or {}).get(platform) or {}


def clear(case, platform, path=None):
    """删掉一条记录（逃生口）。

    为什么需要：`empty` 是 block 级。如果人已经在平台后台补上了封面，
    但台账还留着旧的 `empty`，这个 case 会被**永久拦死**（`--force` 也只绕
    dedupe）。所以要么重跑 `--live` 让记录变成 `ok`，要么在这里删掉重来。
    """
    p = path or LEDGER
    d = load_ledger(p)
    slot = d.get(case) or {}
    if platform in slot:
        del slot[platform]
        if not slot:
            d.pop(case, None)
        _save(d, p)
        return True
    return False


def _age_days(stamp):
    """`at` 字段到今天的整日数；解析不了返回 None。"""
    try:
        t = time.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S")
        return int((time.time() - time.mktime(t)) // 86400)
    except (ValueError, TypeError, OverflowError):
        return None


# ---------------------------------------------------------------- 判据

def cap_of(platform):
    """平台能力；未知平台不猜，返回 None 由调用方报 unknown。"""
    return CAP.get(platform)


def verify(platform, case, path=None, probe=None, max_age=TTL_DAYS):
    """纯判据（默认不联网）：返回一条结论。

    `probe` 传 `(state, evidence)` 用于测试注入；不传则读台账。
    """
    r = {"platform": platform, "case": case, "state": "unknown",
         "field": "", "why": "", "evidence": "", "at": "", "source": "",
         "detail": ""}
    c = cap_of(platform)
    if c is None:
        r["detail"] = "能力表里没有平台 %r ⇒ 不知道该读什么，按未回读处理" % platform
        return r
    r["field"], r["why"] = c["field"], c["why"]

    if probe is not None:
        st, ev = probe
        if st not in STATES:
            st, ev = "unknown", "探针返回了非法状态 %r" % (st,)
        r.update(state=st, evidence=str(ev or ""), source="probe")
        r["detail"] = _phrase(r)
        return r

    if c["mode"] == "none":
        r["state"] = "none"
        r["detail"] = _phrase(r)
        return r
    if c["mode"] == "local":
        r["state"] = "local"
        r["detail"] = _phrase(r)
        return r

    rec = get_record(case, platform, path)
    if not rec:
        r["detail"] = ("台账没有回读记录（能力=%s / 取值字段=%s）⇒ 未回读，"
                       "不能当通过；跑 `--live` 或用发布链路重新记录"
                       % (c["probe"], c["field"] or "?"))
        return r
    r["at"], r["source"] = rec.get("at", ""), rec.get("source", "")
    r["evidence"] = rec.get("evidence", "")
    age = _age_days(r["at"])
    if age is not None and max_age and age > max_age:
        r["state"] = "unknown"
        r["detail"] = ("回读记录已 %d 天（上限 %d）⇒ 降级为未回读，请重跑 --live"
                       % (age, max_age))
        return r
    st = rec.get("state")
    r["state"] = st if st in STATES else "unknown"
    r["detail"] = _phrase(r)
    return r


def _phrase(r):
    """把状态翻成一句人话。`为什么` 始终跟着走 —— 结论位不许只有 OK/FAIL。"""
    st, why, ev = r["state"], r["why"], r["evidence"]
    if st == "ok":
        return "平台侧封面已落库：%s" % (ev[:120] or "（无证据串）")
    if st == "empty":
        return "读了平台字段但封面是空的（%s）%s" % (
            r["field"] or "?", ("：%s" % ev[:120]) if ev else "")
    if st == "local":
        return "平台侧无独立封面字段，无法回读：%s" % why
    if st == "none":
        return "该平台脚本不上封面：%s" % why
    return "未回读：%s" % (r["detail"] or ev or "无记录")


def finding(platform, case, path=None, max_age=TTL_DAYS):
    """转成发布闸格式的 finding；`ok` 返回 None。

    只有 `empty` 是 block。`unknown` 一律 warn ——
    **不拦**是因为「没查过」不等于「有问题」，但**必须说出来**，
    否则闸会变成「没记录 = 通过」的假绿发生器。
    """
    v = verify(platform, case, path=path, max_age=max_age)
    if v["state"] == "ok":
        return None
    if v["state"] == "empty":
        return {"level": "block", "rule": "封面未落库",
                "detail": v["detail"] + "｜人工在平台侧补好后，跑 `scripts/cover_verify.py "
                                        "--case %s --platform %s --clear` 或 `--live` 刷新记录"
                                        % (case, platform)}
    rule = {"unknown": "封面未回读", "local": "封面不可回读",
            "none": "脚本不设封面"}[v["state"]]
    return {"level": "warn", "rule": rule, "detail": v["detail"]}


# ---------------------------------------------------------------- 现场探针

def _norm(s):
    s = re.sub(r"\s+", "", str(s or ""))
    return s.strip("，,。.！!？?：:；;、·—-|｜")


def _md_title(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.startswith("# "):
                    return ln[2:].strip()
    except OSError:
        pass
    return ""


def expected_title(platform, case):
    """该 case 在这个平台上「应该叫什么名字」—— 回读要靠标题认亲。"""
    try:
        if platform == "bilibili":
            import bilibili_publish as BP
            html = BP.load_article_html(case)
            if html:
                return BP.make_title(case, html)
        else:
            import publish_slot as SLOT
            p = SLOT.pack_of(platform, case)
            if p and p.lower().endswith(".md") and os.path.isfile(p):
                return _md_title(p)
    except Exception:                                      # noqa: BLE001
        return ""
    return ""


def _probe_csdn(case, statuses=("draft", "publish", "audit")):
    """CSDN：点 tab 抓页面自己的请求（约 40s/次，别在闸里跑）。"""
    import csdn_list_drafts as LD
    want = _norm(expected_title("csdn", case))
    if not want:
        return "unknown", "拿不到该 case 的 CSDN 期望标题，无法认亲"
    seen = 0
    for st in statuses:
        try:
            rows = LD.list_status(st) or []
        except SystemExit as e:                            # CDP 没起来
            return "unknown", "CSDN 列表读取失败（%s）" % str(e).strip()[:100]
        except Exception as e:                             # noqa: BLE001
            return "unknown", "CSDN 列表读取异常：%s" % str(e)[:100]
        seen += len(rows)
        for it in rows:
            if _norm(it.get("title")) == want:
                cover = (it.get("cover") or "").strip()
                if cover:
                    return "ok", "%s 列表 coverImage=%s" % (st, cover)
                return "empty", "%s 列表找到该文（id=%s）但 coverImage 为空" % (
                    st, it.get("id"))
    return "unknown", "CSDN 三个列表共 %d 条，没找到该标题 ⇒ 无从判断" % seen


def _probe_bilibili(case):
    """B站：草稿接口 origin_image_urls / image_urls（复用已实测定论的内部函数）。"""
    import time as _t
    import bilibili_publish as BP
    import wechat_publish as wp
    want = _norm(expected_title("bilibili", case))
    if not want:
        return "unknown", "拿不到该 case 的 B站期望标题（缺 out/articles/%s.html）" % case
    cdp = None
    tid = None
    try:
        cdp = wp.CDP(BP.PUB_PORT)
        tid = cdp.new_target(BP.BILI_DRAFT_LIST_PAGE)["id"]
        cdp.connect_target(tid)
        _t.sleep(5)
        r = BP._fetch_drafts(cdp, tid)
        if not isinstance(r, dict) or r.get("err") is not None:
            return "unknown", "B站草稿接口异常：%s" % json.dumps(
                r, ensure_ascii=False)[:120]
        drafts = r.get("drafts") or []
        for d in drafts:
            if _norm(d.get("title")) == want:
                u = BP._cover_of(d)
                if u:
                    return "ok", "aid=%s image_urls=%s" % (d.get("article_id"), u)
                return "empty", "aid=%s 草稿在，但 origin_image_urls / image_urls 全空" % (
                    d.get("article_id"))
        return "unknown", "B站草稿箱共 %d 条，没找到该标题" % len(drafts)
    except Exception as e:                                 # noqa: BLE001
        return "unknown", "B站回读失败：%s" % str(e)[:120]
    finally:
        if cdp and tid:
            try:
                cdp.close_target(tid)
            except Exception:                              # noqa: BLE001
                pass


#: 能现场读的平台。其余 live 平台是 `publish-time`（发表时脚本自己判）。
_PROBE = {"csdn": _probe_csdn, "bilibili": _probe_bilibili}


def probe(platform, case, **kw):
    """现场回读并落台账。读不到一律记 `unknown`，绝不记 `ok`。"""
    fn = _PROBE.get(platform)
    if fn is None:
        return "unknown", ("%s 的封面由发表时校验（%s），本模块没有现场探针；"
                           "跑一次发表链路即可写入台账"
                           % (platform, (cap_of(platform) or {}).get("probe")))
    st, ev = fn(case, **kw)
    return st, ev


def verify_live(platform, case, path=None, **kw):
    """`--live`：现场读一次，写台账，再走同一套判据（避免两套口径）。"""
    st, ev = probe(platform, case, **kw)
    record(case, platform, st, ev, source="live", path=path)
    return verify(platform, case, path=path)


# ---------------------------------------------------------------- CLI

def cmd_matrix():
    print("%-13s %-6s %-13s %s" % ("平台", "模式", "读取方式", "取值字段 / 原因"))
    for p in PLATFORMS:
        c = CAP[p]
        print("%-13s %-6s %-13s %s" % (
            p, c["mode"], c["probe"] or "-",
            (c["field"] or c["why"])[:70]))
        if c["field"]:
            print("%-13s %-6s %-13s └ %s" % ("", "", "", c["why"][:70]))
    n = sum(1 for p in PLATFORMS if CAP[p]["mode"] == "live")
    print("\n九平台里只有 %d 个有可回读的封面字段（%s）；"
          "其余 %d 个不能回读，闸只出 warn。" % (
              n, "、".join(p for p in PLATFORMS if CAP[p]["mode"] == "live"),
              len(PLATFORMS) - n))
    return 0


def cmd_report(path=None):
    d = load_ledger(path)
    if not d:
        print("台账为空（%s）⇒ 一条都没回读过" % os.path.relpath(path or LEDGER, ROOT))
        print("先跑一次发表链路，或用 --case X --platform Y --live。")
        return 0
    stat = {}
    for case, slots in sorted(d.items()):
        for p, rec in sorted(slots.items()):
            st = rec.get("state", "?")
            stat[st] = stat.get(st, 0) + 1
            age = _age_days(rec.get("at"))
            print("%-28s %-13s %-7s %s%s" % (
                case, p, st,
                (rec.get("evidence") or "")[:60],
                "  ⚠ %d 天前" % age if age is not None and age > TTL_DAYS else ""))
    print("\n共 %d 条：%s" % (sum(stat.values()),
                            " / ".join("%s %d" % (k, v) for k, v in sorted(stat.items()))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="封面回读（平台侧到底有没有封面）")
    ap.add_argument("--case")
    ap.add_argument("--platform", choices=list(PLATFORMS))
    ap.add_argument("--live", action="store_true", help="现场去平台读一次（要 Chrome 登录态）")
    ap.add_argument("--clear", action="store_true",
                    help="删掉这条记录（人工已在平台侧补好封面时用）")
    ap.add_argument("--report", action="store_true", help="台账全览")
    ap.add_argument("--matrix", action="store_true", help="九平台能力矩阵")
    ap.add_argument("--max-age", type=int, default=TTL_DAYS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.matrix:
        return cmd_matrix()
    if a.report:
        return cmd_report()
    if not (a.case and a.platform):
        ap.error("要 --case + --platform，或 --report / --matrix")

    if a.clear:
        gone = clear(a.case, a.platform)
        print("%s / %s 记录%s" % (a.platform, a.case,
                                 "已删除" if gone else "本来就没有"))
        return 0

    v = (verify_live(a.platform, a.case) if a.live
         else verify(a.platform, a.case, max_age=a.max_age))
    if a.json:
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0 if v["state"] == "ok" else 1
    print("%s / %s" % (a.platform, a.case))
    print("  状态  %s" % v["state"])
    print("  结论  %s" % v["detail"])
    if v["field"]:
        print("  字段  %s" % v["field"])
    if v["at"]:
        print("  记录  %s（%s）" % (v["at"], v["source"]))
    return 0 if v["state"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
