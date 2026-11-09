# -*- coding: utf-8 -*-
"""两朵云「已提交 / 已公开」三态裁定 + `platform_posts` 安全落库（唯一写入口）。

覆盖 `aliyun` 与 `tencentcloud`（2026-11-09 #115 从阿里云专用扩到两朵云）。

**为什么单独一个模块（而不是塞回 yun_finalize）**
  1. 裁定必须只看**匿名公开页**：跳转 `/article/<aid>` 只算「已提交」，
     审核中无登录态 404、带登录态页头「内容审核中」。只有 200 + 标题命中 +
     无审核横幅才落 `published`，否则 `auditing` 且 `published_at=NULL`（PRD #70）。
     腾讯云同一套判据，只换 URL 模板与横幅文案（公开页标题带
     「-腾讯云开发者社区-腾讯云」后缀，`norm()` 包含匹配即可命中）。
  2. 落库只准**单行**：⛔ 不用 `db.upsert_post()`（整行按 JSON 快照覆盖）、
     ⛔ 不用 `Manifest.save()`（整本落盘会洗掉 remote_id/url/draft_id，PRD #82）。
     `_db_sync` 的 `published_at or now` 曾把 draft 行写脏（PRD #109 19:47 事故），
     所以本模块自己拿原生 sqlite3，UPDATE 带写前状态断言、rowcount 必须为 1。
  3. 发布链路（`yun_finalize --publish`）与运维脚本共用同一份裁定与写库，
     避免「一个说 published、一个说 auditing」。

红线：
  · `published_at` 只在 state='published' 时写，且用调用方传入的 `now`（不做时区猜测）；
  · `auditing` / 被驳回时 `url` 留空 —— 公开页此刻打不开，写上去就是编造链接；
  · `url` 只写**公开文章页**（`PUB_URLS` 模板），⛔ 不许写 `write-new?draftId=` 编辑器地址；
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
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 两朵云的**公开文章页**模板（不是编辑器页）：
#   · 阿里云：/article/<aid>
#   · 腾讯云：/article/<aid>（articleId 7 位；draftId 6 位，两者不可混用）
# 🚨 腾讯云 26 行存量里 24 行 `url` 存的是
#    `…/article/write-new?draftId=341947`（建稿时的编辑器地址），既打不开发表文、
#    也不是证据。裁定/回填一律以本表模板为准（2026-11-09 #115）。
PUB_URLS = {
    "aliyun": "https://developer.aliyun.com/article/%s",
    "tencentcloud": "https://cloud.tencent.com/developer/article/%s",
}
# 公开页「还在审核」的横幅文案（平台各说各的，逐平台列，避免把正文里的
# 「审核」二字误判成横幅 — 误判会把真 published 洗成 auditing）
BANNERS = {
    "aliyun": ("内容审核中",),
    "tencentcloud": ("该文章正在审核", "文章正在审核", "内容审核中",
                     "审核通过后可见"),
}
LABEL = {"aliyun": "阿里云", "tencentcloud": "腾讯云"}
SUPPORTED = tuple(PUB_URLS)


def url_of(platform, article_id):
    """公开文章页 URL（platform 不支持就抛错，别静默拼错域名）。"""
    if platform not in PUB_URLS:
        raise ValueError("yun_state 不支持的平台：%r（支持 %s）"
                         % (platform, ", ".join(SUPPORTED)))
    return PUB_URLS[platform] % article_id


def norm(s):
    """比对用归一：去掉所有空白（平台会在标题里插 <br> 与全角空格）。"""
    return re.sub(r"\s+", "", s or "")


# 200 但正文极小 / 无 <title> ⇒ 平台风控插页（实测腾讯云 987 bytes 空标题），
# 它不是「未公开」的证据，必须重试；重试仍如此则只报 unverified，不许当成 auditing 落库。
THROTTLE_MAX_BYTES = 4096


def _opener():
    """无代理 opener：本机 HTTP_PROXY 会污染 127.0.0.1 与阿里云直连（历史踩过）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def anon_get(url, timeout=15, platform="aliyun"):
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
    marks = BANNERS.get(platform, BANNERS["aliyun"])
    soft = (code == 200 and (len(body) < THROTTLE_MAX_BYTES or not title))
    return {"code": code, "bytes": len(body), "title": title,
            "banner": any(k in body for k in marks), "soft": soft,
            "text": re.sub(r"\s+", " ", txt)[:4000]}


def judge(article_id, title, fetch=None, timeout=15, platform="aliyun",
          retries=3, backoff=3.0):
    """按匿名公开页裁定 `published` / `auditing`。

    判据（缺一即 `auditing`）：HTTP 200 + 页面标题包含本稿标题 + 无审核横幅。

    两朵云同一套判据，只换 URL 模板与横幅文案：腾讯云公开页标题形如
    `<标题>-腾讯云开发者社区-腾讯云`，`norm()` 去空白后做**包含**匹配即可命中
    （2026-11-09 实测 4/4）。`fetch` 可注入（单测用），签名同 `anon_get`。
    """
    url = url_of(platform, article_id)
    page, tries = {}, 0
    for tries in range(1, max(1, retries) + 1):
        if fetch is None:
            page = anon_get(url, timeout=timeout, platform=platform)
        else:
            page = fetch(url, timeout=timeout)
        # 「200 + 极小页/无标题」= 风控插页，不是结论，继续重试
        if not page.get("soft"):
            break
        if tries < retries:
            time.sleep(backoff * tries)
    title_ok = bool(norm(title)) and norm(title) in norm(page.get("title"))
    published = (page.get("code") == 200 and title_ok
                 and not page.get("banner"))
    state = "published" if published else "auditing"
    evidence = ("匿名 GET %s：HTTP %s、%s bytes、标题命中=%s、审核横幅=%s"
                % (url, page.get("code"), page.get("bytes"),
                   title_ok, page.get("banner")))
    unverified = page.get("code") == 0 or bool(page.get("soft"))
    if page.get("code") == 0:
        # 网络失败不是证据：仍落 auditing（= 未确认公开），但必须让人一眼看出
        # 「没取到证据」而不是「取到了说没公开」。
        evidence += "（⚠ 网络失败：%s）" % (page.get("error") or "未知")
    elif page.get("soft"):
        evidence += ("（⚠ 疑似风控/限速插页：仅 %s bytes、标题为空，重试 %d 次仍如此"
                     " ⇒ 这不是「未公开」的证据，别据此改状态）"
                     % (page.get("bytes"), tries))
    return {"state": state, "url": url, "code": page.get("code"),
            "bytes": page.get("bytes"), "title": page.get("title") or "",
            "title_ok": title_ok, "banner": bool(page.get("banner")),
            "unverified": unverified, "tries": tries,
            "evidence": evidence, "article_id": str(article_id),
            "platform": platform}


def _row(conn, case, platform="aliyun"):
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "select * from platform_posts where case_id=? and platform=?",
        (case, platform)).fetchone()
    return dict(r) if r else None


def record_publish(platform, case, draft_id, article_id, title,
                   db_path=None, now=None, fetch=None, timeout=15,
                   allow_downgrade=False, verdict=None, reason="",
                   keep_url=False, retries=3, backoff=3.0):
    """发布后落库（两朵云共用）：裁定 → 单行 UPDATE（无行则单行 INSERT）。

    参数
    ----
    verdict : None | "rejected"
        `None` = 按匿名公开页自动裁定；`"rejected"` = **登录态证据**（创作中心
        卡片文案「你的文章已驳回,原因:…」）已确认被驳回，此时落 `draft`
        （与阿里云 #109 同口径；`platform_posts` 没有 `rejected` 态，新增会牵动
        `publish_slot` 的档期统计），`published_at`/`url` 清空，`reason` 进 note。
    keep_url : 仅供存量回填用 —— published 且 url 已是公开页时不重复拼。

    ⚠ 台账写失败**不该**把「文章已发出」变成「发布失败」：本函数抛
    `RuntimeError`，调用方负责降级为告警 + 给出修复入口。

    ⚠ 状态只前进：已 `published` 的行若这次匿名回读只能给 `auditing`
    （风控 404 / 临时下架）或被判驳回，默认**拒绝降级**并抛错，要人工确认后传
    `allow_downgrade=True`。否则一次网络抖动就把真账洗成「未公开」。
    """
    if platform not in SUPPORTED:
        raise ValueError("record_publish 不支持的平台：%r" % (platform,))
    if verdict == "rejected":
        state = "draft"
        j = {"evidence": "登录态创作中心卡片：「%s」" % (reason or "已驳回"),
             "url": "", "code": None, "bytes": 0, "title": "", "title_ok": False,
             "banner": False, "unverified": False,
             "article_id": str(article_id), "platform": platform}
    elif verdict in (None, "published", "auditing"):
        j = judge(article_id, title, fetch=fetch, timeout=timeout,
                  platform=platform, retries=retries, backoff=backoff)
        if verdict:                                   # 显式覆盖（人工复核后）
            j["evidence"] += "（人工指定 %s）" % verdict
        state = verdict or j["state"]
    else:
        raise ValueError("verdict 只能是 None/'published'/'auditing'/'rejected'，"
                         "收到 %r" % (verdict,))

    # 🚨 证据不足不许落账：匿名页没取到有效内容（风控插页 / 网络失败）时，
    #    「auditing」不是结论而是一次失败的取证。写进去会把刚发出的文章记成未公开，
    #    也会把真 published 洗成 auditing 的假象（#115 之后新增）。
    if state == "auditing" and j.get("unverified") and not verdict:
        raise RuntimeError(
            "匿名公开页没取到有效证据（疑似风控/限速或网络失败）：%s\n"
            "  ⇒ 未写台账。稍后重跑 python scripts/yun_state.py %s '<标题>' --platform %s"
            % (j.get("evidence"), article_id, platform))

    now = now or time.strftime("%Y-%m-%d %H:%M:%S")
    # auditing / rejected：公开页此刻打不开，写上去就是编造链接
    url = "" if state != "published" else j.get("url") or url_of(platform, article_id)
    published_at = now if state == "published" else None
    extra = "" if state == "published" else "（published 需 200 + 标题命中 + 无审核横幅；url 待过审后独立回读再补）"
    note = ("[%s %s发布] 草稿 %s → 文章 %s；%s ⇒ %s%s"
            % (now[:10], LABEL.get(platform, platform), draft_id, article_id,
               j.get("evidence") or "", state, extra))

    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    try:
        old = _row(conn, case, platform)
        if (old and (old.get("state") or "") == "published"
                and state != "published"):
            if not allow_downgrade:
                raise RuntimeError(
                    "拒绝把已 published 的行降级为 %s（证据：%s）。"
                    "复查 article id 是否传错 / 页面是否临时不可见；确认要降级请显式 "
                    "allow_downgrade=True" % (state, j.get("evidence")))
            note += "｜⚠ 人工确认降级（原 published_at=%s）" % (old.get("published_at") or "")
        if old:
            fields = ("state", "remote_id", "url", "published_at", "draft_id",
                      "title", "note", "updated_at")
            old_url = old.get("url") or ""
            vals = (state, str(article_id),
                    (old_url if (keep_url and state == "published" and old_url)
                     else url),
                    published_at, str(draft_id),
                    title or old.get("title") or "", note, now)
            cur = conn.execute(
                "update platform_posts set " + ", ".join("%s=?" % f for f in fields)
                + " where case_id=? and platform=? and state=?",
                vals + (case, platform, old.get("state")))
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
                " values (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (case, platform, state, str(article_id), url,
                 title or "", str(draft_id), published_at, now, note))
            if cur.rowcount != 1:
                conn.rollback()
                raise RuntimeError("台账单行 INSERT rowcount=%s（期望 1）⇒ 已回滚"
                                   % cur.rowcount)
        conn.commit()
        new = _row(conn, case, platform)
    finally:
        conn.close()

    if not new or new.get("state") != state or new.get("remote_id") != str(article_id):
        raise RuntimeError("台账回读与写入不一致：期望 state=%r remote_id=%r，实际 %r"
                           % (state, str(article_id),
                              new and {k: new.get(k) for k in ("state", "remote_id")}))
    if state == "published" and not (new.get("url") or "").startswith("http"):
        raise RuntimeError("published 但 url 不是公开页：%r" % (new.get("url"),))
    return {"state": state, "evidence": j.get("evidence"), "judge": j,
            "before": old, "after": new, "platform": platform,
            "changed": sorted(k for k in new if (old or {}).get(k) != new[k])}


def record_aliyun_publish(case, draft_id, article_id, title, **kw):
    """阿里云专用薄封装（历史调用点/单测不动签名）。"""
    return record_publish("aliyun", case, draft_id, article_id, title, **kw)


def record_tencent_publish(case, draft_id, article_id, title, **kw):
    """腾讯云专用薄封装。article_id 是 **articleId（7 位）**，不是 draftId（6 位）。"""
    return record_publish("tencentcloud", case, draft_id, article_id, title, **kw)


def main():
    """命令行只读裁定：python scripts/yun_state.py <article_id> [标题] [--platform tc]"""
    import sys
    args = [a for a in sys.argv[1:]]
    platform = "aliyun"
    for flag in ("--platform", "-p"):
        if flag in args:
            i = args.index(flag)
            platform = args[i + 1] if i + 1 < len(args) else platform
            del args[i:i + 2]
    if platform in ("tc", "tencent", "qcloud"):
        platform = "tencentcloud"
    if not args:
        print(__doc__)
        return 2
    j = judge(args[0], args[1] if len(args) > 1 else "", platform=platform)
    print(json.dumps(j, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
