#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号「远端 ⇄ 本地」对账：把已发表/群发、草稿箱的真实状态拉回来，修正本地台账。

为什么必须有这个（2026-10-05，用户提出）
----------------------------------------
**你在后台点「群发」，本地永远不知道。**

根因链条：
  1. 公众号是个人订阅号，**没有 freepublish API 权限**，群发只能后台手动点
     （项目README 自己写了这件事，见 wechat_publish.py 模块头）。
  2. 所以 `status=published` 只可能在**本地跑 publish 时**写入。
  3. 你在后台点群发 ⇒ 本地台账**毫无感知**，仍记着 `status=draft`。
  4. 后果：下次 refresh 会去刷一条**已经发表、不在草稿箱**的文章——
     白等几分钟拿不到，还可能误判成「掉登录」；更糟的是让人以为草稿丢了。

⚠ 实测两个关键事实（别再重复踩）：
  - `appmsg?action=list_ex&type=10`（正式文章列表）**返回空**
    `{"app_msg_cnt":0}` ⇒ 已发表**不能**从草稿箱接口顺带拿到。
  - 已发表要走**另一个接口** `cgi-bin/appmsgpublish?sub=list`，见
    `fetch_published_remote()`。

**核心原则：远端是唯一真相源（remote is truth）。**
本地台账只是缓存，任何「本地记着、远端没有/变了」都应以远端为准。
这个模块只做两件事，**不碰远端任何写操作**：
  1. 拉两侧真值（草稿箱 + 已发表）；
  2. 与 `data/wechat_published.json` 三方对账，输出差异；
     `--apply` 时才把远端已发表回写本地 `status`。

四态判定（别把 UNKNOWN 当成"已删"）
-----------------------------------
  OK             远端有、本地记的一致
  FIX            有确凿证据，本地错了（可 --apply）
  WARN           证据不足，只能提示人工核对
  UNKNOWN        **接口没读准** ⇒ 一律不下结论、不改台账

⚠⚠ 为什么 UNKNOWN 必须单独一态：把「接口挂了」误判成「远端没有」，
sync 会建议删掉其实还在的条目 —— 那比不同步危险得多。
所以**只有草稿箱和已发表列表都读成功**才敢判 REMOTE_MISSING。

认亲优先级（2026-10-05 定）
------------------------
  1. `appmsgid` / `appmsg_id` —— 群发后服务端沿用原草稿 id，**最可靠**；
  2. 标题精确（归一化后）—— 老记录可能没存 id，只能靠标题反查。
⚠ 草稿箱字段叫 `appmsgid`、已发表列表叫 `appmsg_id`（**带下划线**），
   两边拼写不一致，对账时别混。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

PUBLISHED_PATH = os.path.join(ROOT, "data", "wechat_published.json")

# 远端一次未必真给这么多（可能被服务端截断），所以两边都分页 + 去重。
PAGE = 10
MAX_PAGES = 30                      # 最多翻 300 条，远端不可能更多


# ======================================================================
# 远端拉取
# ======================================================================
def _xhr_json(cdp, url):
    """在 mp 页面上下文里发同步 XHR 并解析 JSON。"""
    js = ("(function(){var x=new XMLHttpRequest();"
          "x.open('GET',%s,false);x.send(null);"
          "return x.responseText;})()" % json.dumps(url))
    raw = cdp.eval(js, refresh_context=True)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def fetch_draft_remote(cdp, tok):
    """拉**草稿箱**全量（type=77）。返回 (dict[appmsgid] -> item, ok)。

    ⚠ 分页去重是必须的：`count=N` 不保证真给 N 条（实测 begin=30 就返回 0）。
    只按 begin=0 拉一次会把「分页截断」误判成「远端没有」——
    这正是那9 条缺失案例一度查不清的原因。
    """
    out = {}
    for begin in range(0, PAGE * MAX_PAGES, PAGE):
        url = ("https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_ex&type=77"
               "&sub=all&begin=%d&count=%d&token=%s&lang=zh_CN"
               "&f=json&ajax=1&random=0.7" % (begin, PAGE, tok))
        d = _xhr_json(cdp, url)
        if not isinstance(d, dict):
            # 第一页就挂 ⇒ 明确失败，**不能当成「远端为空」**
            if begin == 0:
                return {}, False
            break
        if d.get("base_resp", {}) and d["base_resp"].get("ret") not in (0, None):
            if begin == 0:
                return {}, False
            break
        items = d.get("app_msg_list") or []
        if not items:
            break
        for it in items:
            aid = it.get("appmsgid")
            #⚠ 不能写 `if aid:` —— appmsgid 恰好是 0 时会被静默丢掉
            # （真bug：分页测试里就靠这条抓到，少一条会误判成「远端少了一条」）
            if aid is not None and str(aid) != "":
                out[str(aid)] = it
        # 这一页没填满 ⇒ 后面没有了，直接停（少打一轮请求，也少一次限流风险）
        if len(items) < PAGE:
            break
    return out, True


def fetch_published_remote(cdp, tok):
    """拉**已发表/群发**记录。返回 (list[dict], ok)。

    ⚠⚠⚠ **三层 JSON，且字段名和草稿箱完全不同**（2026-10-05 实测dump）：
    ```
    顶层:      {"base_resp":…, "is_admin":…, "publish_page": "<JSON字符串>"}
    publish_page: {"total_count":12,"publish_count":1,"masssend_count":11,
                   "featured_count":0,"item_show_type_counts":[…],
                   "publish_list":[{"publish_type":1,"publish_info":"<JSON字符串>"}]}
                                                             ↑ 元素只有这2 个字段
    publish_info: {"type":10002,"msgid":2247484064,"appmsg_info":[…],
                   "sent_result":{…},"batch_members":[…],…}
    appmsg_info[0]: {"appmsgid":2247484064,"title":"…","digest":"…",
                     "content_url":"https://mp.weixin.qq.com/s/…",…}
    ```
    ⚠ **`publish_list` 元素里没有 `appmsg_id`，也没有 `title`** ——
    只写 `it.get("title")` 会永远取到 None，12 条会被去重撞成 1 条
    （这个bug 真的发生过一次，害我以为「远端只群发了 1 篇」）。
    ⇒ 必须 `json.loads(publish_info)` 再取 `appmsg_info[0].title`。

    ⚠⚠ **群发后 appmsgid 会变**：本地台账记的是**草稿** id（如 100000162），
    已发表列表里是**新** id（如 2247484064）。⇒ **已发表侧不能靠 id认亲，
    只能靠标题**（`match_remote` 里id 匹配对已发表注定miss，靠标题兜住）。

    实测规模：total_count=12（publish_count=1 单篇 + masssend_count=11 群发）。
    """
    out = []
    seen = set()
    for begin in range(0, PAGE * MAX_PAGES, PAGE):
        url = ("https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list"
               "&search_field=null&begin=%d&count=%d&token=%s"
               "&lang=zh_CN&f=json&ajax=1" % (begin, PAGE, tok))
        d = _xhr_json(cdp, url)
        if not isinstance(d, dict):
            if begin == 0:
                return [], False
            break
        pp = d.get("publish_page")
        if isinstance(pp, str):
            try:
                pp = json.loads(pp)
            except Exception:
                return [], False
        if not isinstance(pp, dict):
            if begin == 0:
                return [], False
            break
        lst = pp.get("publish_list") or []
        if not lst:
            break
        for it in lst:
            for art in _flatten_publish_item(it):
                key = str(art.get("appmsg_id") or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                out.append(art)
        # 一页没填满 ⇒ 后面没有了
        if len(lst) < PAGE:
            break
    return out, True


def _flatten_publish_item(it):
    """把 publish_list 的一条展开成 [{appmsg_id,title,…}]。

    一条 `publish_list` 可能对应**多篇**文章（多图文的头条/次条），
    它们的标题是独立的 ⇒ 都得参与认亲，否则会漏判「本地还记着 draft」。
    ⚠ 解析失败返回空列表（宁可少认一条，也不能拿错标题去改台账）。
    """
    pi = it.get("publish_info")
    if isinstance(pi, str):
        try:
            pi = json.loads(pi)
        except Exception:
            return []
    if not isinstance(pi, dict):
        return []
    out = []
    for a in (pi.get("appmsg_info") or []):
        if not isinstance(a, dict):
            continue
        out.append({
            "appmsg_id": a.get("appmsgid"),
            "title": a.get("title") or a.get("digest") or "",
            "content_url": a.get("content_url") or "",
            "publish_type": it.get("publish_type"),
            "batch_msgid": pi.get("msgid"),
            "is_deleted": a.get("is_deleted"),
        })
    return out


# ======================================================================
# 纯函数：归一化 + 认亲 + 判定
# ======================================================================
def norm_title(s):
    """标题归一化：去所有空白（含全角/不间断空格）后转小写。

    ⚠ 必须归一化：远端标题里常有全角空格、不间断空格，本地是半角，
    直接比会满屏都是「标题变了」假警报（这是踩过的坑）。
    """
    if not s:
        return ""
    return "".join(ch for ch in str(s) if not ch.isspace()
                   and ch != "　").lower()


def build_title_index(cases_by_id, local, title_matcher=None):
    """建「归一化标题 -> case_id」索引。返回 (want, dup)。

    ⚠ `title_matcher(cid, case) -> 标题` 由调用方注入（默认用
    wechat_publish.make_wechat_title），这样本模块不依赖发布脚本也能单测。
    台账里的旧标题只作兜底，且不覆盖期望标题。
    """
    want = {}
    dup = set()
    if title_matcher is not None:
        for cid, c in cases_by_id.items():
            try:
                t = title_matcher(cid, c)
            except Exception:
                t = None
            if not t:
                continue
            k = norm_title(t)
            if not k:
                continue
            if k in want and want[k] != cid:
                dup.add(cid)
                dup.add(want[k])
                continue
            want[k] = cid
    for cid, rec in (local or {}).items():
        t = (rec or {}).get("title")
        if not t:
            continue
        k = norm_title(t)
        # 台账旧标题只作兜底：仅在该标题**尚未被别人占用**时才登记，
        # 否则会把期望标题挤掉（实测踩过：case 改了标题后旧标题仍占着 key，
        # 导致远端老草稿认亲到错的地方）。
        if k and k not in want:
            want[k] = cid
    return want, dup


def match_remote(remote_draft, remote_pub, local, cases_by_id,
                 title_matcher=None):
    """把远端草稿/已发表认亲到 case id。

    返回 dict：
      draft_by_cid   {cid: appmsgid}
      pub_by_cid     {cid: pub_record}
      draft_orphan   [(appmsgid, title)]远端草稿认不出 case
      pub_orphan     [(appmsg_id, title)]  远端已发表认不出 case
      title_dup      {cid} 本地自己标题撞车，对账不可靠
    """
    want, dup = build_title_index(cases_by_id, local, title_matcher)
    draft_by_cid, draft_orphan = {}, []
    for aid, it in (remote_draft or {}).items():
        cid = None
        # 1) 优先按台账里存的 appmsgid 反查 —— 群发后 id 不变，最可靠
        for lc, rec in (local or {}).items():
            if str(rec.get("appmsgid") or "") == str(aid):
                cid = lc
                break
        if not cid:
            cid = want.get(norm_title(it.get("title")))
        if cid:
            draft_by_cid[cid] = str(aid)
        else:
            draft_orphan.append((str(aid), it.get("title") or ""))

    pub_by_cid, pub_orphan = {}, []
    for it in (remote_pub or []):
        pid = str(it.get("appmsg_id") or it.get("appmsgid") or "")
        cid = None
        for lc, rec in (local or {}).items():
            if pid and str(rec.get("appmsgid") or "") == pid:
                cid = lc
                break
        if not cid:
            cid = want.get(norm_title(it.get("title")))
        if cid:
            pub_by_cid[cid] = it
        else:
            pub_orphan.append((pid, it.get("title") or ""))
    return {
        "draft_by_cid": draft_by_cid,
        "pub_by_cid": pub_by_cid,
        "draft_orphan": draft_orphan,
        "pub_orphan": pub_orphan,
        "title_dup": dup,
    }


def classify(local, remote_draft, remote_pub, cases_by_id,
             draft_ok=True, pub_ok=True, title_matcher=None):
    """三方对账。返回 diffs: [(level, code, cid, detail)]。

    level: FIX / WARN / INFO / UNKNOWN
    ⚠ 纯函数：不碰文件、不碰网络，所有判定都能单测。

    判定规则（每一格都要有**双侧证据**才敢下结论）：
      · 本地 published + 远端已发表有 ⇒ OK
      · 本地 published + 已发表列表读到了但没有 ⇒ WARN（不猜，可能超列表范围）
      · 本地 published + 已发表列表**没读到** ⇒ UNKNOWN
      · 本地 draft/partial + 草稿箱有 ⇒ OK
      · 本地 draft/partial + 草稿箱无 + 已发表有 ⇒ FIX DRAFT_GONE_BUT_PUBLISHED
      · 本地 draft/partial + 两侧都读成功且都没有 ⇒ FIX REMOTE_MISSING
      · 任一侧读取失败 ⇒ UNKNOWN，**绝不判 REMOTE_MISSING**
    """
    m = match_remote(remote_draft, remote_pub, local, cases_by_id,
                     title_matcher)
    draft_by_cid = m["draft_by_cid"]
    pub_by_cid = m["pub_by_cid"]
    diffs = []

    for cid in sorted(m["title_dup"]):
        diffs.append((
            "UNKNOWN", "TITLE_COLLISION", cid,
            "本地有两条案例归一化后标题相同 ⇒ 按标题认亲不可靠，"
            "请人工核对（不会自动改台账）"))

    for cid, rec in sorted((local or {}).items()):
        rec = rec or {}
        st = rec.get("status")
        aid = draft_by_cid.get(cid)
        pub = pub_by_cid.get(cid)
        in_draft = aid is not None
        in_pub = pub is not None

        if st == "published":
            if in_pub:
                continue                                   # OK
            if not pub_ok:
                diffs.append((
                    "UNKNOWN", "PUB_LIST_UNREADABLE", cid,
                    "本地记已发表，但已发表列表没读到 ⇒ 无法核实，"
                    "不改台账"))
            else:
                diffs.append((
                    "WARN", "PUB_NOT_IN_LIST", cid,
                    "本地记已发表，但远端已发表列表里没有它"
                    "（台账 published_at=%s）—— 可能超出列表范围，"
                    "也可能台账记错，**不自动改**，请人工核对"
                    % (rec.get("published_at") or "-")))
            continue

        # 本地记的是 draft / partial /其它
        if in_draft:
            continue                                       # OK，草稿还在
        if in_pub:
            diffs.append((
                "FIX", "DRAFT_GONE_BUT_PUBLISHED", cid,
                "草稿箱已无、但已在已发表列表里（群发后新 appmsg_id=%s，"
                "链接 %s）⇒ "
                "你后台群发过，本地 status 应改为 published"
                % (pub.get("appmsg_id") or "-",
                   (pub.get("content_url") or "")[:48])))
            continue
        # 两侧都没有
        if not (draft_ok and pub_ok):
            code = "DRAFT_LIST_UNREADABLE" if not draft_ok \
                else "PUB_LIST_UNREADABLE"
            diffs.append((
                "UNKNOWN", code, cid,
                "本地记着、远端没认出来，但%s ⇒ **无法区分"
                "「真没了」和「没读到」**，不改台账"
                % ("草稿箱没读到" if not draft_ok else "已发表列表没读到")))
            continue
        diffs.append((
            "FIX", "REMOTE_MISSING", cid,
            "远端草稿箱与已发表列表都没有它（本地记 appmsgid=%s）⇒ "
            "远端已不存在，本地记着是历史残留"
            % (rec.get("appmsgid") or "-")))

    for aid, title in m["draft_orphan"]:
        diffs.append((
            "INFO", "REMOTE_ORPHAN", "",
            "远端草稿认不出对应 case：appmsgid=%s%s"
            % (aid, ("　标题=%s" % title[:30]) if title else "")))
    for pid, title in m["pub_orphan"]:
        diffs.append((
            "INFO", "PUB_ORPHAN", "",
            "远端已发表认不出对应 case：appmsg_id=%s%s"
            % (pid, ("　标题=%s" % title[:30]) if title else "")))
    return diffs


# ======================================================================
# 编排 + 输出 + 回写
# ======================================================================
def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M")


def load_local():
    if not os.path.exists(PUBLISHED_PATH):
        return {}
    with open(PUBLISHED_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_local(pub, backup=True, path=None):
    """原子写 + 改前备份。

    ⚠⚠ 改 `data/*.json` 前**必须**留副本：这个文件是唯一的发布台账，
    一次手滑覆盖掉就再也对不上远端了。红线 7：改单字段用最小写入。

    ⚠⚠⚠ `path` 参数**只为单测存在**。曾经单测直接调 `apply_fixes()`，
    结果它内部走 `save_local()` 把**真实的 data/wechat_published.json
    覆盖成1 条假数据**（`.bak` 也被第二次调用一并覆盖，连备份都没救回，
    最后靠 `git show HEAD:` 才恢复的 41 条）。
    ⇒ 任何测试都必须传 `path=tmp`；这里保留参数是为了让这条红线可测。
    """
    target = path or PUBLISHED_PATH
    if backup and os.path.exists(target):
        bak = target + ".bak"
        with open(target, "rb") as f:
            raw = f.read()
        with open(bak, "wb") as f:
            f.write(raw)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pub, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def render_report(diffs, remote_n_draft, remote_n_pub, local_n,
                  draft_ok=True, pub_ok=True):
    lines = []
    lines.append("=" * 72)
    lines.append("公众号远端 ⇄ 本地对账（只读，不改任何远端数据）")
    lines.append("=" * 72)
    lines.append("远端草稿箱 %d 条%s · 远端已发表 %d 条%s · 本地台账 %d 条"
                 % (remote_n_draft, "" if draft_ok else "（读取失败）",
                    remote_n_pub, "" if pub_ok else "（读取失败）", local_n))
    lines.append("")
    if not diffs:
        lines.append("完全一致，无需改动。")
        return "\n".join(lines)
    order = {"FIX": 0, "WARN": 1, "UNKNOWN": 2, "INFO": 3}
    cnt = {}
    for lvl, code, cid, detail in sorted(
            diffs, key=lambda x: (order.get(x[0], 9), x[2], x[1])):
        cnt[lvl] = cnt.get(lvl, 0) + 1
        lines.append("[%-7s] %-26s %-16s %s"
                     % (lvl, code, cid or "-", detail))
    lines.append("")
    lines.append("合计：FIX %d · WARN %d · UNKNOWN %d · INFO %d"
                 % (cnt.get("FIX", 0), cnt.get("WARN", 0),
                    cnt.get("UNKNOWN", 0), cnt.get("INFO", 0)))
    lines.append("")
    lines.append("含义：FIX=有确凿证据本地错了（--apply 才改）"
                 " / WARN=证据不足请人工核对 / "
                 "UNKNOWN=接口没读准，**别当已删**")
    return "\n".join(lines)


def run_sync(cdp, tok, local=None, cases_by_id=None, title_matcher=None):
    """拉两侧 + 判定。返回 (diffs, local, meta)。**不落盘**。"""
    if local is None:
        local = load_local()
    remote_draft, draft_ok = fetch_draft_remote(cdp, tok)
    remote_pub, pub_ok = fetch_published_remote(cdp, tok)
    diffs = classify(local, remote_draft, remote_pub, cases_by_id or {},
                     draft_ok=draft_ok, pub_ok=pub_ok,
                     title_matcher=title_matcher)
    return diffs, local, {
        "remote_draft": remote_draft, "remote_pub": remote_pub,
        "draft_ok": draft_ok, "pub_ok": pub_ok,
    }


def apply_fixes(diffs, local, path=None):
    """回写本地。返回 (改published 条数, 标missing 条数)。

    ⚠ **只改 status，不动 title/appmsgid**：标题可能是你在后台改过的，
    自动覆盖会丢掉你的编辑。
    ⚠ WARN / UNKNOWN 一律不动（证据不足）。
    ⚠ REMOTE_MISSING 只**加** `missing` 标记、不删记录 —— 那些 appmsgid
    是历史证据，删了没法追溯"当初发过什么"。
    ⚠⚠ `path` 只给单测用（真实数据文件绝不能被测试写坏，见 `save_local`）。
    """
    n_pub = n_missing = 0
    for lvl, code, cid, _ in diffs:
        rec = local.get(cid)
        if not rec:
            continue
        if lvl == "FIX" and code == "DRAFT_GONE_BUT_PUBLISHED":
            if rec.get("status") == "published":
                continue
            rec["status"] = "published"
            rec["group_sent"] = True
            rec["synced_at"] = _now()
            rec["sync_note"] = "远端已发表列表里存在，本地由 sync 回写"
            n_pub += 1
        elif lvl == "FIX" and code == "REMOTE_MISSING":
            if rec.get("missing"):
                continue
            rec["missing"] = True
            rec["missing_synced_at"] = _now()
            n_missing += 1
    if n_pub or n_missing:
        save_local(local, path=path)
    return n_pub, n_missing