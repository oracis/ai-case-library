#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""被平台拦/封之后的处置：标记 → 剔除草稿 → 顺延下一篇。

**规则（2026-10-28 用户定，不是猜的）：**

1. 某平台的稿子被拦下/封掉（审核不通过、内容违规、限流下架）；
2. 先按平台给的改进意见改一版、重新发；
3. 改完**仍然被拦** → 该稿在该平台记 `blocked`（终止态，不再自动重试）；
4. **顺延发同平台队列里的下一篇补上**（保证每平台仍有稿子发出）；
5. **把被拦的稿从该平台草稿箱剔除**（不留残稿占位，否则下次去重/回读
   校验都会被它干扰）。

为什么不能只记 `failed` 了事：`failed` 是"这次挂了、下轮重试"，
被拦的稿重试一次还是被拦，会永久堵在队头，把后面所有稿子挡住 ——
而"每平台一篇"的节奏就断了。所以它必须是和 `failed` 分开的终止态。

**顺延谁**，沿用群里既有的两条口径（`next_target()`）：

* 公众号 —— 按文章编号 `data/issue_numbers.json` 从小到大（用户口径：
  "公众号依照文章编号发布"）；
* 头条/小红书/B站/掘金 —— 草稿箱里**存得最久**的那篇（用户口径："其他
  的依照存入草稿箱最久那篇发布"），台账时间 `at` 最早者优先。

**剔除怎么删**，各平台能力不同，全部走它们各自已有的命令/API，不新造轮子：

| 平台 | 手段 | 需要的 id |
|---|---|---|
| 公众号 | `wechat_publish.py delete --appmsgid` | `wechat_published.json[cid].appmsgid` |
| 头条 | `toutiao_publish.py dedup <标题> --to 0` | 标题 |
| 小红书 | `xhs_publish.py purge --match <标题> --all` | 标题 |
| B站 | `bili_draft_api.delete_draft(article_id)` | `bili_drafts.json[*].article_id` |
| 掘金 | `juejin_publish.py delete --draft-id` | `juejin_drafts.json[cid].draft_id` |

删除**不可逆**，所以 `delete_draft()` 默认 `dry=True`，真删必须显式传
`dry=False`（CLI 上是 `--yes`），且删前把"将要删的标题"打出来。
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
DATA = os.path.join(ROOT, "data")

PLATS = ("wechat", "toutiao", "xhs", "bilibili", "juejin")

LABEL = {"wechat": "公众号", "toutiao": "今日头条", "xhs": "小红书",
         "bilibili": "B站", "juejin": "掘金"}


# ---- 小工具 ---------------------------------------------------------------
def _rj(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _ids_of(obj):
    """published 台账可能是 dict{cid:...} 也可能是 list[{id:...}]，统一取 id 集。"""
    out = set()
    if isinstance(obj, dict):
        out |= set(obj)
    elif isinstance(obj, list):
        for e in obj:
            if isinstance(e, dict) and e.get("id"):
                out.add(e["id"])
    return out


def _run(args, timeout=300):
    """跑子命令。**必须清掉代理**：本机 127.0.0.1 的 CDP 会被 http_proxy
    劫持成 502（2026-10-28 实测踩过），所以这里一律剥掉代理变量。"""
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost"
    env["PYTHONIOENCODING"] = env["PYTHONUTF8"] = "utf-8"
    p = subprocess.run([sys.executable] + args, cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _tail(text, n=400):
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


# ---- 草稿身份：删它需要什么 ------------------------------------------------
def draft_ref(plat, cid):
    """返回删草稿所需的引用信息（dict）；拿不到就返回 {} 并说明缺什么。"""
    if plat == "wechat":
        rec = (self_wechat() or {}).get(cid) or {}
        aid = str(rec.get("appmsgid") or "").strip()
        return {"appmsgid": aid} if aid else {}
    if plat == "juejin":
        rec = _rj(os.path.join(DATA, "juejin_drafts.json"), {}).get(cid) or {}
        did = str(rec.get("draft_id") or "").strip()
        return {"draft_id": did} if did else {}
    if plat == "bilibili":
        for e in _rj(os.path.join(DATA, "bili_drafts.json"), []) or []:
            if isinstance(e, dict) and e.get("id") == cid:
                aid = str(e.get("article_id") or "").strip()
                if aid:
                    return {"article_id": aid, "title": e.get("title", "")}
        return {}
    # 头条/小红书只能按标题包含匹配删 —— 用稳定的产品名当匹配键
    t = match_key(cid, plat)
    return {"match": t} if t else {}


def self_wechat():
    return _rj(os.path.join(DATA, "wechat_published.json"), {})


def title_of(cid, plat):
    """该平台实际用的标题：**平台产物优先**，清单兜底，最后退案例名。

    为什么产物优先（2026-10-28 踩过）：`publish_manifest.json` 里存的是
    **上一次存草稿时的标题**，改了覆盖标题后它就是旧值。实测
    quran-unlock 在清单里还是「Quran Unlock：移动 app」，而实际提交的是
    `out/toutiao/<id>/meta.json` 里的「Quran Unlock：古兰经习惯打卡」。
    删草稿按标题匹配，用旧标题就是删不掉、还留个残稿。
    """
    if plat == "toutiao":
        meta = _rj(os.path.join(ROOT, "out", "toutiao", cid, "meta.json"), {})
        if isinstance(meta, dict) and meta.get("title"):
            return str(meta["title"])
    if plat == "xhs":
        note = _rj(os.path.join(ROOT, "out", "xhs", cid, "note.json"), {})
        if isinstance(note, dict) and note.get("title"):
            return str(note["title"])
    from multiplatform import state as S
    t = S.Manifest().title_of(cid, plat)
    if t:
        return t
    for c in (_rj(os.path.join(DATA, "cases.json"), []) or []):
        if isinstance(c, dict) and c.get("id") == cid:
            return c.get("name") or cid
    return cid


def match_key(cid, plat):
    """给「按标题包含匹配」的删除用（头条 dedup / 小红书 purge）。

    用案例名（=平台标题里的产品名前缀，如 `Quran Unlock`）比整条标题更稳：
    标题后半截会随精修/截断变化，产品名不变；包含匹配也不会因为多了
    「：古兰经习惯打卡」而落空。名字太短（<4 字符）时退回完整标题，
    免得匹配到一堆无关草稿。
    """
    for c in (_rj(os.path.join(DATA, "cases.json"), []) or []):
        if isinstance(c, dict) and c.get("id") == cid:
            name = str(c.get("name") or "").strip()
            if len(name) >= 4:
                return name
            break
    return title_of(cid, plat)


# ---- 剔除草稿 -------------------------------------------------------------
def delete_draft(plat, cid, dry=True):
    """把 cid 在该平台的草稿删掉。返回 (ok, detail)。

    dry=True 只打印"会删什么"，不真动远端 —— 删除不可逆。
    """
    ref = draft_ref(plat, cid)
    lab = LABEL.get(plat, plat)
    if not ref:
        return False, ("拿不到 %s 的草稿引用（台账里没有 appmsgid/draft_id/"
                       "article_id，或产物标题缺失），未删" % lab)

    what = ", ".join("%s=%s" % (k, v) for k, v in ref.items())
    if dry:
        return True, "[dry] 将删除 %s 草稿：%s（%s）" % (lab, cid, what)

    if plat == "wechat":
        rc, out = _run([os.path.join(SCRIPTS, "wechat_publish.py"),
                        "delete", "--appmsgid", ref["appmsgid"]])
    elif plat == "juejin":
        rc, out = _run([os.path.join(SCRIPTS, "juejin_publish.py"),
                        "delete", "--draft-id", ref["draft_id"]])
    elif plat == "toutiao":
        # dedup 只删同标题的重复份，--to 0 表示该标题一条不留
        rc, out = _run([os.path.join(SCRIPTS, "toutiao_publish.py"),
                        "dedup", ref["match"], "--to", "0"])
    elif plat == "xhs":
        rc, out = _run([os.path.join(SCRIPTS, "xhs_publish.py"),
                        "purge", "--match", ref["match"], "--all"])
    elif plat == "bilibili":
        rc, out = _bili_delete(ref["article_id"])
    else:
        return False, "未知平台 %s" % plat

    ok = (rc == 0)
    detail = "%s 草稿删除%s：%s" % (lab, "成功" if ok else "失败", _tail(out, 300))
    if not ok and plat == "wechat":
        # cmd_delete 自己不自启浏览器（只有 send/publish 会），9222 没起就会
        # 在 _connect_mp 里直接抛错。这里把话说明白，别让人对着报错猜。
        detail += ("\n    （公众号删稿要求 9222 调试 Chrome 已启动并登录 mp.weixin.qq.com："
                   "先跑 scripts/launch_chrome_cdp.py，再重试本条）")
    return ok, detail


def _bili_delete(article_id):
    """B站没有单篇删除的子命令，直接调它的 draft/delete API。"""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import bilibili_publish as B, chrome_cdp_launch as _L\n"
        "import bili_draft_api as API\n"
        "B.wp._autostart_chrome_if_needed(B.PUB_PORT, profile=_L.PLAT_PROFILE)\n"
        "cdp = B.wp.CDP(B.PUB_PORT)\n"
        "B._api_tab(cdp)\n"
        "API.delete_draft(cdp, %r)\n"
        "print('bili draft %s deleted')\n" % (SCRIPTS, article_id, article_id)
    )
    return _run(["-c", code])


# ---- 顺延：下一条该发谁 ---------------------------------------------------
def next_target(plat, manifest=None):
    """按平台口径取「下一条该发的」。返回 (cid, title, why) 或 (None, "", why)。

    排除：已发布、已 blocked、无草稿记录。
    """
    from multiplatform import state as S
    m = manifest or S.Manifest()

    def usable(cid):
        return not (m.is_published(cid, plat) or m.is_blocked(cid, plat))

    if plat == "wechat":
        # 按编号：草稿箱还躺着、编号最小的那篇
        issues = _rj(os.path.join(DATA, "issue_numbers.json"), {})
        led = self_wechat()
        cand = []
        for cid, rec in led.items():
            if not isinstance(rec, dict):
                continue
            if str(rec.get("status") or "") == "published":
                continue
            n = issues.get(cid)
            if n and usable(cid):
                cand.append((n, cid, rec.get("title", "")))
        cand.sort()
        if not cand:
            return None, "", "公众号草稿箱里没有「有编号且未发布」的稿子"
        n, cid, t = cand[0]
        return cid, t or title_of(cid, plat), "第%d篇（编号最小未发布）" % n

    # 其余平台：草稿箱里存得最久的那篇
    if plat == "toutiao":
        rows = _rj(os.path.join(DATA, "toutiao_drafts.json"), []) or []
        pub = _rj(os.path.join(DATA, "toutiao_published.json"), {})
    elif plat == "xhs":
        rows = _rj(os.path.join(DATA, "xhs_drafts.json"), []) or []
        pub = _rj(os.path.join(DATA, "xhs_published.json"), [])
    elif plat == "bilibili":
        rows = _rj(os.path.join(DATA, "bili_drafts.json"), []) or []
        pub = _rj(os.path.join(DATA, "bili_published.json"), {})
    elif plat == "juejin":
        d = _rj(os.path.join(DATA, "juejin_drafts.json"), {}) or {}
        rows = [{"id": k, "at": v.get("created_at", ""),
                 "title": v.get("title", "")} for k, v in d.items()
                if isinstance(v, dict)]
        pub = _rj(os.path.join(DATA, "juejin_published.json"), {})
    else:
        return None, "", "未知平台 %s" % plat

    published = _ids_of(pub) | {c for c in (m.cids() or [])
                                if m.is_published(c, plat)}
    rows = [r for r in rows
            if isinstance(r, dict) and r.get("id")
            and r["id"] not in published and usable(r["id"])]
    rows.sort(key=lambda r: str(r.get("at") or r.get("created_at") or ""))
    if not rows:
        return None, "", "%s 没有可顺延的草稿（未发布的都发完了）" % \
                         LABEL.get(plat, plat)
    r = rows[0]
    return (r["id"], r.get("title") or title_of(r["id"], plat),
            "草稿箱存得最久（%s）" % (r.get("at") or r.get("created_at") or "?"))


# ---- 主流程 ---------------------------------------------------------------
def handle(plat, cid, reason="", delete=True, dry_delete=True, manifest=None):
    """标记 blocked + 剔除草稿，返回 (next_cid, next_title, next_why)。"""
    from multiplatform import state as S
    m = manifest or S.Manifest()
    title = title_of(cid, plat)
    m.mark_blocked(cid, plat, reason or "平台拦截（未记录原因）", title)
    print("已标记 %s / %s → blocked《%s》" % (LABEL.get(plat, plat), cid, title))
    if reason:
        print("  原因：%s" % reason)
    if delete:
        ok, detail = delete_draft(plat, cid, dry=dry_delete)
        print("  %s" % detail)
    else:
        print("  （按 --keep-draft 保留远端草稿，未剔除）")
    return next_target(plat, m)
