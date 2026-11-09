# -*- coding: utf-8 -*-
"""阿里云「已提交 / 已公开」三态裁定 + `platform_posts` 安全落库（唯一写入口）。

**为什么单独一个模块（而不是塞回 yun_finalize）**
  1. 裁定必须只看**匿名公开页**：跳转 `/article/<aid>` 只算「已提交」，
     审核中无登录态 404、带登录态页头「内容审核中」。只有 200 + 标题命中 +
     无审核横幅才落 `published`，否则 `auditing` 且 `published_at=NULL`（PRD #70）。
  2. 落库只准**单行**：⛔ 不用 `db.upsert_post()`（整行按 JSON 快照覆盖）、
     ⛔ 不用 `Manifest.save()`（整本落盘会洗掉 remote_id/url/draft_id，PRD #82）。
     `_db_sync` 的 `published_at or now` 曾把 draft 行写脏（PRD #109 19:47 事故），
     所以本模块自己拿原生 sqlite3，UPDATE 带写前状态断言、rowcount 必须为 1。
  3. 发布链路（`yun_finalize --publish`）与运维脚本共用同一份裁定与写库，
     避免「一个说 published、一个说 auditing」。

红线：
  · `published_at` 只在 state='published' 时写，且用调用方传入的 `now`（不做时区猜测）；
  · `auditing` 时 `url` 留空 —— 公开页此刻 404，写上去就是编造链接；
  · 只动目标行，写完由调用方 / 自检逐字段比对。
"""
import html
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "ledger.db")
PUB_URL = "https://developer.aliyun.com/article/%s"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def norm(s):
    """比对用归一：去掉所有空白（平台会在标题里插 <br> 与全角空格）。"""
    return re.sub(r"\s+", "", s or "")


def _opener():
    """无代理 opener：本机 HTTP_PROXY 会污染 127.0.0.1 与阿里云直连（历史踩过）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def anon_get(url, timeout=15):
    """匿名（无登录态、无代理）GET，返回公开页可判定字段。"""
    op = _opener()
    op.addheaders = [("User-Agent", UA)]
    try:
        r = op.open(url, timeout=timeout)
        body = r.read().decode("utf-8", "replace")
        code = r.status
    except urllib.error.HTTPError as e:                    # 404 = 未公开
        return {"code": e.code, "bytes": 0, "title": "", "banner": False,
                "text": ""}
    except Exception as e:                                 # noqa: BLE001
        return {"code": 0, "bytes": 0, "title": "", "banner": False,
                "text": "", "error": str(e)[:120]}
    m = re.search(r"<title>(.*?)</title>", body, re.S)
    title = html.unescape(m.group(1)).strip() if m else ""
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    return {"code": code, "bytes": len(body), "title": title,
            "banner": "内容审核中" in body, "text": re.sub(r"\s+", " ", txt)[:4000]}


def judge(article_id, title, fetch=None, timeout=15):
    """按匿名公开页裁定 `published` / `auditing`。

    判据（缺一即 `auditing`）：HTTP 200 + 页面标题包含本稿标题 + 无「内容审核中」。
    `fetch` 可注入（单测用），签名同 `anon_get`。
    """
    url = PUB_URL % article_id
    page = (fetch or anon_get)(url, timeout=timeout)
    title_ok = bool(norm(title)) and norm(title) in norm(page.get("title"))
    published = (page.get("code") == 200 and title_ok
                 and not page.get("banner"))
    state = "published" if published else "auditing"
    evidence = ("匿名 GET %s：HTTP %s、%s bytes、标题命中=%s、审核横幅=%s"
                % (url, page.get("code"), page.get("bytes"),
                   title_ok, page.get("banner")))
    return {"state": state, "url": url, "code": page.get("code"),
            "bytes": page.get("bytes"), "title": page.get("title") or "",
            "title_ok": title_ok, "banner": bool(page.get("banner")),
            "evidence": evidence, "article_id": str(article_id)}


def _row(conn, case, platform="aliyun"):
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "select * from platform_posts where case_id=? and platform=?",
        (case, platform)).fetchone()
    return dict(r) if r else None


def record_aliyun_publish(case, draft_id, article_id, title,
                          db_path=None, now=None, fetch=None, timeout=15,
                          allow_downgrade=False):
    """发布后落库：裁定 → 单行 UPDATE（无行则单行 INSERT）→ 返回前后快照。

    ⚠ 台账写失败**不该**把「文章已发出」变成「发布失败」：本函数抛
    `RuntimeError`，调用方负责降级为告警 + 给出修复入口。

    ⚠ 状态只前进：已 `published` 的行若这次匿名回读只能给 `auditing`
    （风控 404 / 临时下架），默认**拒绝降级**并抛错，要人工确认后传
    `allow_downgrade=True`。否则一次网络抖动就把真账洗成「未公开」。
    """
    j = judge(article_id, title, fetch=fetch, timeout=timeout)
    state = j["state"]
    now = now or time.strftime("%Y-%m-%d %H:%M:%S")
    url = j["url"] if state == "published" else ""     # auditing 公开页 404，不写链接
    published_at = now if state == "published" else None
    note = ("[%s 阿里云发布] 草稿 %s → 文章 %s；%s ⇒ %s"
            "（published 需 200 + 标题命中 + 无审核横幅；auditing 的 url "
            "待过审后独立回读再补）"
            % (now[:10], draft_id, article_id, j["evidence"], state))

    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    try:
        old = _row(conn, case, "aliyun")
        if (old and (old.get("state") or "") == "published"
                and state != "published" and not allow_downgrade):
            raise RuntimeError(
                "拒绝把已 published 的行降级为 %s（匿名回读：%s）。"
                "复查 aid 是否传错 / 页面是否临时不可见；确认要降级请显式 "
                "allow_downgrade=True" % (state, j["evidence"]))
        if old:
            fields = ("state", "remote_id", "url", "published_at", "draft_id",
                      "title", "note", "updated_at")
            vals = (state, str(article_id), url, published_at, str(draft_id),
                    title or old.get("title") or "", note, now)
            cur = conn.execute(
                "update platform_posts set " + ", ".join("%s=?" % f for f in fields)
                + " where case_id=? and platform='aliyun' and state=?",
                vals + (case, old.get("state")))
            if cur.rowcount != 1:
                conn.rollback()
                raise RuntimeError(
                    "台账单行 UPDATE rowcount=%s（期望 1；写前 state=%r）⇒ 已回滚，"
                    "先跑 scripts/repair_manifest_fields.py 或人工核对再重试"
                    % (cur.rowcount, old.get("state")))
        else:
            cur = conn.execute(
                "insert into platform_posts (case_id, platform, state, remote_id,"
                " url, title, draft_id, attempt, published_at, updated_at, note)"
                " values (?, 'aliyun', ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (case, state, str(article_id), url,
                 title or "", str(draft_id), published_at, now, note))
            if cur.rowcount != 1:
                conn.rollback()
                raise RuntimeError("台账单行 INSERT rowcount=%s（期望 1）⇒ 已回滚"
                                   % cur.rowcount)
        conn.commit()
        new = _row(conn, case, "aliyun")
    finally:
        conn.close()

    if not new or new.get("state") != state or new.get("remote_id") != str(article_id):
        raise RuntimeError("台账回读与写入不一致：期望 state=%r remote_id=%r，实际 %r"
                           % (state, str(article_id),
                              new and {k: new.get(k) for k in ("state", "remote_id")}))
    return {"state": state, "evidence": j["evidence"], "judge": j,
            "before": old, "after": new,
            "changed": sorted(k for k in new if (old or {}).get(k) != new[k])}


def main():
    """命令行只读裁定：python scripts/yun_state.py <article_id> [标题]"""
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    j = judge(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    print(json.dumps(j, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
