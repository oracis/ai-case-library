# -*- coding: utf-8 -*-
"""阿里云「草稿 → 发布 → 回读 → 台账」单篇一条命令入口（正式入口，2026-11-09 转正）。

**与全平台发布主链路的关系（P0 已定，别再造第二个发布入口）**
  全平台单一源仍是 `publish_one.py`（档期→选稿→十一闸→发布→回读→台账）。
  阿里云在这里是个**受控例外**，原因是人机验证：`aliyunCaptcha` 滑块必须由人
  在可见窗口里拖，任何「无人值守自动发」都是假绿。本脚本只承担三件主链路缺的事：

  1. `publish_aliyun()` 自己不查配额 —— 裸调等于绕闸。#66/#70 之后
     「当日配额」是真硬闸，`--force` 只绕 dedupe，**这里必须显式拦**。
  2. `publish_aliyun()` 只把结果写进 `data/yun_finalize.json` 的
     `__community__<draft>` 键（该前缀会跳过 `_db_sync`），SQLite 真值不会动。
     本脚本负责按**阿里云三态**落 `platform_posts`：只有公开页 200 +
     标题命中 + 无审核横幅才算 published，否则 state='auditing' 且
     published_at=NULL（⛔ 跳转 /article/<aid> 只算「已提交」）。
  3. 台账写盘只走**单行 UPDATE**（rowcount==1 + 写前状态断言），
     ⛔ 不用 upsert_post / Manifest.save() / 整本重建（`_db_sync` 的
     `upsert_post` + `published_at or now` 曾把 draft 行写脏，见 #109）。

**为什么暂时不能直接 `publish_one --platform aliyun`（P1 待解，不是本脚本的锅）**
  `publish_guard.check_dupe()` 见到台账 `draft_id` 非空就 `ok=False`（语义是
  「别再建第二份」），可两朵云的正道恰恰是「发这一份已有草稿」⇒ 主链路在这条
  路径上天生过不去。P1 计划：dedupe 对 {aliyun,tencentcloud} + 显式
  `--al-draft/--tc-draft` 降级为 warn；把本脚本的三态回读与单行 UPDATE 抽成
  `scripts/yun_state.py` 供 `yun_finalize` 调用；`publish_one` 支持 `--al-draft`
  覆盖。**做完 P1 就删掉本文件**，不留双入口。

**闸不重复实现**：配额/候选/判重口径一律取自 `publish_slot` / `publish_guard` /
`publish_one` 的同一份实现，本脚本只做「人工滑块 + 三态裁定 + 安全落库」。

用法（配额重置后，人在电脑前）：
    python -X utf8 scripts/aliyun_publish_go.py --case gojiberryai --dry
    python -X utf8 scripts/aliyun_publish_go.py --case gojiberryai --go --captcha-wait 600

--dry 只读：配额、草稿回读、本地稿/封面/社区计划，不打开编辑器、不写盘。
--go  真发：配额通过 → 起有头 9228 → publish_aliyun（等人工拖滑块）
      → 匿名回读 → 单行台账 → 与备份逐字段比对。
"""
import argparse
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"

DB = os.path.join(ROOT, "data", "ledger.db")
YF = os.path.join(ROOT, "data", "yun_finalize.json")
OUT = os.path.join(ROOT, "tmp")

# aid 是 #110 现场建出来的草稿（与本地改稿逐字一致，已双回读）；
# 2026-11-09 19:04（#113 P0）台账 draft_id 已对齐到这两个值，旧值
# 1708661 / 1700270 指向 #111 已删草稿，别再拿回来。
PLAN = {
    "gojiberryai": {
        "aid": "1710840",
        "md": os.path.join(ROOT, "drafts", "newbatch", "aliyun",
                           "gojiberryai.md"),
        "cover": os.path.join(ROOT, "out", "toutiao", "gojiberryai",
                              "cover.png"),
    },
    "chessigma": {
        "aid": "1711205",
        "md": os.path.join(ROOT, "drafts", "newbatch", "aliyun",
                           "chessigma.md"),
        "cover": os.path.join(ROOT, "out", "toutiao", "chessigma",
                              "cover.png"),
    },
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

DETAIL_JS = """
fetch("/developer/api/articleDraft/draftDetail?aid=%s", {credentials:"include"})
 .then(function(r){return r.text().then(function(t){return r.status+"|"+t;});})
"""


def norm(s):
    return re.sub(r"\s+", "", s or "")


# ---------------------------------------------------------------- 只读工具

def port_up(port=9228):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        op.open("http://127.0.0.1:%d/json/version" % port, timeout=2).read()
        return True
    except Exception:                                       # noqa: BLE001
        return False


def anon_get(url):
    """无登录态、无代理的匿名 GET（公开页唯一可信证据）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    op.addheaders = [("User-Agent", UA)]
    try:
        r = op.open(url, timeout=15)
        body = r.read().decode("utf-8", "replace")
        code = r.status
    except urllib.error.HTTPError as e:                     # 404 = 未公开
        return {"code": e.code, "bytes": 0, "title": "", "banner": False,
                "text": ""}
    except Exception as e:                                  # noqa: BLE001
        return {"code": 0, "bytes": 0, "title": "", "banner": False,
                "text": "", "error": str(e)[:120]}
    m = re.search(r"<title>(.*?)</title>", body, re.S)
    title = html.unescape(m.group(1)).strip() if m else ""
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"\s+", " ", txt)
    return {"code": code, "bytes": len(body), "title": title,
            "banner": "内容审核中" in body, "text": txt[:4000]}


def draft_detail(cdp, aid, retries=4):
    """draftDetail 回读 + 空数据重试（#111 实测批量连读会瞬时返回空）。"""
    for i in range(retries):
        raw = cdp.eval(DETAIL_JS % aid) or ""
        code, _, payload = raw.partition("|")
        try:
            d = json.loads(payload).get("data") or {}
        except Exception:                                   # noqa: BLE001
            d = {}
        if isinstance(d, dict) and (d.get("title") or d.get("content")):
            body = html.unescape(d.get("content") or "")
            return {"aid": str(d.get("aid") or aid), "http": code,
                    "title": d.get("title") or "", "content": body,
                    "content_len": len(body),
                    "abstract": (d.get("abstractContent") or ""),
                    "cover": d.get("coverUrl") or "",
                    "retries": i}
        time.sleep(1.2)
    return {"aid": aid, "http": "retry-failed", "title": "", "content": "",
            "content_len": 0, "abstract": "", "cover": "", "retries": retries}


def quota():
    import publish_slot as PS                                 # noqa: PLC0415
    return PS.decide("aliyun")


def md_title_body(path):
    from csdn_publish import split_md                          # noqa: PLC0415
    title, body = split_md(open(path, encoding="utf-8").read())
    return title.strip(), body


def backup_db(tag):
    dst = os.path.join(OUT, "ledger_backup_aliyun_pub_%s.db" % tag)
    src = sqlite3.connect(DB)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def rows_of(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "select * from platform_posts where platform='aliyun' order by id")]
    finally:
        c.close()


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, choices=sorted(PLAN))
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--community", default="云原生",
                    help="发布子社区（历史 7/7 全用「云原生」）")
    ap.add_argument("--captcha-wait", type=int, default=600)
    ap.add_argument("--use-existing", action="store_true",
                    help="允许复用已在线的 9228（必须确认它是可见的有头窗口）")
    a = ap.parse_args()
    if not (a.dry or a.go):
        ap.error("必须给 --dry 或 --go")

    case = a.case
    plan = PLAN[case]
    aid = plan["aid"]
    title, body = md_title_body(plan["md"])

    print("[1/6] 时间 / 配额")
    print("   now   =", time.strftime("%Y-%m-%d %H:%M:%S %z"))
    q = quota()
    print("   decide = quota %s / today_count %s | %s"
          % (q.get("quota"), q.get("today_count"), q.get("reason")))
    quota_ok = q.get("today_count", 1) < q.get("quota", 1) and not q.get("frozen")
    print("   quota_ok =", quota_ok,
          "（aliyun 日配额 %s，today_count %s %s）"
          % (q.get("quota"), q.get("today_count"),
             "⇒ 明天再来" if not quota_ok else ""))

    print("[2/6] 本地稿 / 封面 / 计划")
    print("   md      =", os.path.relpath(plan["md"], ROOT), len(body), "字")
    print("   title   =", title)
    print("   cover   =", os.path.relpath(plan["cover"], ROOT),
          "exists=", os.path.isfile(plan["cover"]))
    print("   aid     =", aid, "| community =", a.community)

    print("[3/6] 草稿回读（只读 draftDetail）")
    import yun_finalize as Y                                  # noqa: PLC0415
    proc = None
    cdp = None
    if port_up():
        if a.go and not a.use_existing:
            raise SystemExit(
                "✗ 9228 已在线。请先关掉它（可能是无头实例，滑块你看不到），"
                "或确认窗口可见后加 --use-existing。")
        cdp = Y._CDP(9228)
        print("   复用已在线 9228")
    else:
        if a.go:
            print("   起有头 Chrome（9228，profile=chrome-yun-profile）")
            proc, cdp = Y._connect(headed=True)
        else:
            print("   起只读 Chrome（无头，仅读 draftDetail）")
            proc, cdp = Y._connect(headed=False)

    ev = {"case": case, "aid": aid, "title": title, "at": ""}
    try:
        # ⚠ `_connect()` 只建了 browser 级 WS、没 attach page ⇒ 直接 eval 会
        #   `NoneType.send_text` 崩（yun_finalize 里也有同注释）。
        #   先落一个**列表页**（不是编辑器，避免触发平台自动建草稿）。
        cdp.open("https://developer.aliyun.com/my#/article")
        time.sleep(6)
        d = draft_detail(cdp, aid)
        print("   HTTP=%s title=%r len=%d cover=%r abstract=%d 字"
              % (d["http"], d["title"][:40], d["content_len"],
                 d["cover"][:40], len(d["abstract"])))
        checks = {
            "draft_http_200": d["http"] == "200",
            "draft_title_matches_md": norm(d["title"]) == norm(title),
            "draft_body_nonempty": d["content_len"] > 0,
            "draft_aid_matches": d["aid"] == aid,
            "local_cover_exists": os.path.isfile(plan["cover"]),
            "quota_allows": quota_ok,
        }
        print("   预检:", json.dumps(checks, ensure_ascii=False))
        ev["draft"] = {k: v for k, v in d.items() if k != "content"}
        ev["prechecks"] = checks
    finally:
        if not a.go:
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:                           # noqa: BLE001
                    pass
            try:
                if cdp is not None and getattr(cdp, "bws", None) is not None:
                    cdp.bws.close()
            except Exception:                               # noqa: BLE001
                pass

    if not a.go:
        hard = ("quota_allows", "draft_http_200", "draft_title_matches_md",
                "draft_body_nonempty", "draft_aid_matches", "local_cover_exists")
        bad = [k for k in hard if not checks.get(k)]
        print("\n[dry] %s ｜ 未通过：%s"
              % ("全绿，可 --go" if not bad else "有阻断项", bad or "无"))
        json.dump(ev, open(os.path.join(OUT, "aliyun_publish_%s_plan.json" % case),
                           "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return 0 if not bad else 1

    # ---------------- 真发 ----------------
    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise SystemExit("✗ 预检未过 %s，拒绝发布（不会点「发布文章」）" % bad)

    print("[4/6] 发布（滑块请在可见窗口里人工拖；等待上限 %ds）"
          % a.captcha_wait)
    t0 = time.time()
    try:
        new_aid = Y.publish_aliyun(
            cdp, int(aid), a.community, plan["cover"],
            captcha_wait=a.captcha_wait, body_md=body, title=title,
            abstract=Y.first_paragraph(body))
    except SystemExit as e:
        # 没见过跳转：最常见是「发布子社区/摘要」等必填项空值让客户端校验
        # 静默拦下（点了没反应、无请求），其次是二次风控。
        raise SystemExit("✗ 未见跳转：%s\n"
                         "  排查：① 窗口里看是否还有滑块/确认框未点；"
                         "② 「发布子社区」是否已选（%s）；"
                         "③ 摘要是否为空。⛔ 不要立刻重跑（验证码会刷新、"
                         "上次拖的作废），先看窗口再决定。" % (e, a.community))
    finally:
        try:
            if cdp is not None and getattr(cdp, "bws", None) is not None:
                cdp.bws.close()
        except Exception:                                   # noqa: BLE001
            pass
    print("   跳转 aid=%s ｜ 耗时 %.0fs" % (new_aid, time.time() - t0))
    ev["new_aid"] = str(new_aid)

    print("[5/6] 独立回读（匿名 GET 是唯一判据；跳转只算「已提交」）")
    url = "https://developer.aliyun.com/article/%s" % new_aid
    pub = anon_get(url)
    title_ok = norm(title) in norm(pub["title"])
    print("   匿名 HTTP=%s bytes=%s banner=%s title=%r"
          % (pub["code"], pub["bytes"], pub["banner"], pub["title"][:60]))
    print("   标题命中=%s" % title_ok)
    state = "published" if (pub["code"] == 200 and title_ok
                            and not pub["banner"]) else "auditing"
    print("   裁定 state =", state,
          "（published 需 200 + 标题命中 + 无「内容审核中」）")
    ev["readback"] = {"url": url, "code": pub["code"], "bytes": pub["bytes"],
                      "banner": pub["banner"], "title": pub["title"],
                      "title_ok": title_ok, "state": state}

    # 登录态二次回读（公共页被风控时补一条内部证据；不改裁定）
    print("[6/6] 台账（单行 UPDATE）")
    tag = time.strftime("%Y%m%d-%H%M%S")
    bak = backup_db(tag)
    ev["backup"] = os.path.relpath(bak, ROOT)
    before = {r["case_id"]: r for r in rows_of(bak)}
    old = before.get(case) or {}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    note = ("[%s aliyun 发布] 草稿 %s → 文章 %s；匿名 GET HTTP %s、标题命中=%s、"
            "审核横幅=%s ⇒ %s"
            % (now[:10], aid, new_aid, pub["code"], title_ok, pub["banner"], state))
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute(
            "update platform_posts set state=?, remote_id=?, url=?, "
            "published_at=?, draft_id=?, title=?, note=?, updated_at=? "
            "where case_id=? and platform='aliyun' and state=?",
            (state, str(new_aid),
             url if state == "published" else (old.get("url") or ""),
             now if state == "published" else None,
             aid, title, note, now, case, old.get("state")))
        if cur.rowcount != 1:
            conn.rollback()
            raise SystemExit("✗ 台账 UPDATE rowcount=%s（期望 1），已回滚"
                             % cur.rowcount)
        conn.commit()
    finally:
        conn.close()

    # yun_finalize.json 同步（不走 ledger_set：它会触发 _db_sync 二次写库）
    from atomicio import write_json                            # noqa: PLC0415
    yf = json.load(open(YF, encoding="utf-8")) if os.path.isfile(YF) else {}
    rec = yf.get(case) or {}
    rec.update({"al_draft": aid, "al_article": str(new_aid),
                "al_state": state,
                "al_state_at": now,
                "al_published_at": now if state == "published" else "",
                "community": a.community,
                "updated": now})
    yf[case] = rec
    write_json(YF, yf)
    ev["ledger"] = {"state": state, "remote_id": str(new_aid),
                    "draft_id": aid, "published_at": now if state == "published" else None}

    # 复核：只有目标行变了
    after = {r["case_id"]: r for r in rows_of(DB)}
    drift = []
    for cid, r in after.items():
        o = before.get(cid) or {}
        diff = sorted(k for k in r if o.get(k) != r[k])
        if diff:
            drift.append((cid, diff))
    print("   变化行：", drift)
    print("   目标行：", json.dumps({k: after[case][k] for k in
                                  ("case_id", "state", "remote_id", "url",
                                   "draft_id", "published_at")},
                                 ensure_ascii=False))
    q2 = quota()
    print("   发布后 decide = quota %s / today_count %s"
          % (q2.get("quota"), q2.get("today_count")))

    rpt = os.path.join(OUT, "aliyun_publish_%s_report.json" % case)
    json.dump(ev, open(rpt, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[产物]", os.path.relpath(rpt, ROOT), "| 备份",
          os.path.relpath(bak, ROOT))
    print("[结论]", "已公开" if state == "published"
          else "已提交、审核中（published_at 留空，过审后需独立匿名回读再补）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
