#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台「发布失败定性」诊断：PUBLISHED / MISSING / RATE_LIMIT / UNKNOWN。

## 为什么有这个模块（2026-10-17）

2026-10-16 在公众号上踩过一次大坑：visualizee-ai 的草稿从服务端消失后，
refresh 只报「编辑器未就绪(320003)」—— 限流 / 你后台群发过 / 草稿被删，
三种成因在页面上长得一模一样。人肉排查花了半小时（拉 48 条草稿列表 +
标题关键词 0 命中 + 探针 + 问用户），最后答案还是「早群发了」。

`wechat_sync.classify` + `wechat_publish._verdict_for` 把公众号那套排查
固化了，但**只有公众号有**。头条 / B站 / 小红书 publish 失败时仍然只
抛一句「标题框聚焦失败」，用户只能自己开浏览器看 —— 于是又变成
「凭记忆答误删，实际早已群发」。

本模块把同一套判据抽到四平台共用：**四态是同一套判据，不同的只是
「怎么读草稿列表 / 已发布列表」**。

## 四态判据（务必读，别把后两种混成前两种）

| verdict | 含义 | 该做什么 |
|---|---|---|
| `PUBLISHED` | 草稿箱无 + 已发布列表有同标题 ⇒ **你后台群发过** | 台账标 published（有双侧证据，可回写） |
| `MISSING` | 两侧接口都读成功、且都没有 ⇒ 草稿真没了（死篇号） | 只能重建（publish --case X），重试无用 |
| `RATE_LIMIT` | 草稿还在箱里、只是写不进去 ⇒ 限流或记录级损坏 | 等几分钟重试 |
| `UNKNOWN` | 任一侧接口没读准 ⇒ **不下任何结论** | 宁可重跑也不要误判 |

⚠ **比朴素 classify 严一档**：判 PUBLISHED 要求**双侧都读成功**。
草稿箱读失败时远端为空，此时「草稿箱无」不是证据 —— 把它当证据就会把
一次网络抖动误判成「你群发过」，比不判更坏。

⚠ **别混淆「拿不到」和「没有」**：`list_drafts` 抛异常 = 没读到
（⇒ UNKNOWN），返回 `[]` = 读到了且真的没有。B站/头条的 API 在风控
抖动时会返 `{"code":-101}` 而不是抛异常 —— 调用方必须看返回的是空列表
还是 None，`None` 一律当没读到。

## 各平台读法（2026-10-17 实证）

| 平台 | 草稿列表 | 已发布列表 |
|---|---|---|
| wechat | `wechat_sync` 的草稿接口 | `appmsgpublish?sub=list` |
| toutiao | `GET /mp/agw/creator_center/draft_list?type=0&count=200&app_id=1231` | `Page.navigate` 到 `/api/feed/mp_provider/v1/`（**只能导航式，同源 fetch 被 argus 挡**） |
| bilibili | `GET /x/dynamic/feed/article/draft/list` | `GET /x/space/article?mid=<mid>`（免签空间公开数据） |
| xiaohongshu | ❌ 无服务端草稿 API（离开编辑页自动暂存到浏览器本地 IndexedDB，实测 0 个 XHR） | ❌ 未探 |

⇒ 小红书恒返回 UNKNOWN + 说明文案。这是**诚实的未知**，不是失败：
早报「你本地浏览器里有草稿但我们看不到」比瞎猜有用。

## 用法

```python
import reconcile_verdict as rv
p = rv.probe(cdp, "toutiao")            # {"draft_titles": [...], "pub_titles": [...],
                                        #  "draft_ok": bool, "pub_ok": bool, "why": "..."}
v, detail = rv.verdict_for(p, cid, case)
# v in ("PUBLISHED", "MISSING", "RATE_LIMIT", "UNKNOWN")
```

或者直接给用户看：

```python
print(rv.explain("toutiao", cid, case, p))
```
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import wechat_publish as wp          # noqa: E402  CDP / 案例加载 的公共底座
import chrome_cdp_launch as _L       # noqa: E402  PLAT_PROFILE（四平台共用 profile）

PLATFORMS = ("wechat", "toutiao", "bilibili", "xiaohongshu")

VERDICTS = ("PUBLISHED", "MISSING", "RATE_LIMIT", "UNKNOWN")

# verdict -> 该做什么（人话，直接给用户看）
NEXT_ACTION = {
    "PUBLISHED": "台账已标「已发布」（有草稿箱缺失 + 已发表列表双侧证据），不用重发",
    "MISSING": "两侧接口都读到了、都没有 ⇒ 草稿真的没了，重试无用，"
               "得重新 publish 一次",
    "RATE_LIMIT": "草稿还在箱里、只是这次写不进去 ⇒ 限流或记录级损坏，"
                  "等 3-5 分钟重跑同一命令即可",
    "UNKNOWN": "接口没读准 ⇒ 本次不下结论（绝不据此说「草稿被删」），"
               "重跑一次；仍失败请开浏览器自己看一眼",
}


def _norm(s):
    """标题归一化：去空白、全角转半角、小写。"""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "", s)
    return s.replace("：", ":").replace("，", ",")


def _match_local(cid, case, remote_titles, title_maker=None):
    """本地 case 是否命中远端标题列表。返回命中的标题或 None。

    判据三级（从强到弱，命中即返）：
      1. 产品名 / case id 子串（跨口径最稳 —— 2026-10-16 P0 实测远端
         已全改成「产品名：描述」新口径，而 make_*_title 生成的仍是
         「月收$xK：…」旧口径，字面前 N 字符匹配必然失配）；
      2. 前缀互补（远端用缩写/简称时子串失效，如 PromptMon↔
         promptmonitor-io、Divine↔divine-widgets；长度≥5 防误伤）；
      3. 本地生成标题的前 12 字符（给没产品名的标题兜底）。
    """
    if not remote_titles:
        return None
    keys = set()
    core = re.sub(r"-(io|ai|app|co|com|us|net|b-v|9e3eaa|[0-9a-f]{6})$",
                  "", cid or "")
    if len(core) >= 4:
        keys.add(core.lower())
    nm = (case.get("name") or "").strip() if isinstance(case, dict) else ""
    if nm:
        head = re.split(r"[：:]", nm)[0].strip()
        if len(head) >= 4:
            keys.add(head.lower())
        if len(nm) >= 4:
            keys.add(nm.lower())
    if len(cid or "") >= 5:
        keys.add(cid.lower())

    norm = [(_norm(t), t) for t in remote_titles if t]
    for k in keys:
        for tl, orig in norm:
            if k in tl:
                return orig
    for tl, orig in norm:
        words = [w for w in re.split(r"[^a-z0-9]+", tl) if len(w) >= 5]
        for k in keys:
            for w in words:
                if w.startswith(k) or k.startswith(w):
                    return orig
    if title_maker is not None:
        try:
            key = _norm(title_maker(case))[:12]
        except Exception:                                  # noqa: BLE001
            key = ""
        if len(key) >= 6:
            for tl, orig in norm:
                if key in tl:
                    return orig
    return None


# ---------------------------------------------------------------- 探针

def _probe_toutiao(cdp):
    import toutiao_publish as tt
    r = {"draft_titles": None, "pub_titles": None,
         "draft_ok": False, "pub_ok": False, "why": ""}
    try:
        raw = tt.fetch_draft_api_raw(cdp)
        if raw is None:
            r["why"] = "头条 draft_list 没读到"
        else:
            r["draft_titles"] = [t for t, _ in
                                 (tt.fetch_draft_api(cdp) or [])]
            r["draft_ok"] = True
    except Exception as e:                                  # noqa: BLE001
        r["why"] = "头条草稿箱异常：%s" % str(e)[:80]
    try:
        arts = tt.fetch_published_articles(cdp, max_page=3)
        # 空列表要区分「读到了真的空」和「没读到」：tt 版把两种都返 []，
        # 靠 why 通道补判据 —— 页面打不开时 arts 为空且没有 title。
        r["pub_titles"] = [a.get("title") for a in arts if a.get("title")]
        r["pub_ok"] = bool(r["pub_titles"]) or bool(arts) or not r["why"]
    except Exception as e:                                  # noqa: BLE001
        r["why"] = (r["why"] + " / " if r["why"] else "") + \
                   "头条已发表列表异常：%s" % str(e)[:80]
    return r


def _probe_bilibili(cdp):
    import bili_draft_api as ba
    r = {"draft_titles": None, "pub_titles": None,
         "draft_ok": False, "pub_ok": False, "why": ""}
    try:
        drafts = ba.list_drafts(cdp)
        r["draft_titles"] = [d.get("title") for d in drafts if d.get("title")]
        r["draft_ok"] = True
    except Exception as e:                                  # noqa: BLE001
        r["why"] = "B站 draft/list 异常：%s" % str(e)[:80]
    try:
        arts = ba.list_published_articles(cdp, max_page=3)
        r["pub_titles"] = [a.get("title") for a in arts if a.get("title")]
        r["pub_ok"] = True                    # 该接口免签，返回 [] 就是真没有
    except Exception as e:                                  # noqa: BLE001
        r["why"] = (r["why"] + " / " if r["why"] else "") + \
                   "B站 x/space/article 异常：%s" % str(e)[:80]
    return r


def _probe_wechat(cdp, published=None, cases_by_id=None):
    """公众号走已有的 `wechat_sync.run_sync`（含分页 + 三层 JSON 解析）。"""
    import wechat_publish as wp
    import wechat_sync as ws
    r = {"draft_titles": None, "pub_titles": None,
         "draft_ok": False, "pub_ok": False, "why": ""}
    published = published if published is not None else wp.load_published()
    cases_by_id = cases_by_id or {}
    try:
        tok = wp._get_token(cdp)
    except Exception as e:                                  # noqa: BLE001
        r["why"] = "公众号 token 没拿到：%s" % str(e)[:80]
        return r
    try:
        diffs, meta = ws.run_sync(cdp, tok, local=published,
                                  cases_by_id=cases_by_id,
                                  title_matcher=lambda cid: wp.make_wechat_title(
                                      cases_by_id.get(cid, {"id": cid})))
        r["draft_ok"] = bool(meta.get("draft_ok"))
        r["pub_ok"] = bool(meta.get("pub_ok"))
        r["diffs"] = diffs
        r["meta"] = meta
        if not (r["draft_ok"] and r["pub_ok"]):
            side = "草稿箱" if not r["draft_ok"] else "已发表列表"
            r["why"] = "%s没读到" % side
    except Exception as e:                                  # noqa: BLE001
        r["why"] = "公众号对账异常：%s" % str(e)[:80]
    return r


def _probe_xiaohongshu(cdp):
    return {"draft_titles": None, "pub_titles": None,
            "draft_ok": False, "pub_ok": False,
            "why": "小红书没有服务端草稿 API（离开编辑页自动暂存到浏览器"
                   "本地 IndexedDB，实测 0 个 XHR）⇒ 无法对账，恒 UNKNOWN"}


_PROBES = {"toutiao": _probe_toutiao,
           "bilibili": _probe_bilibili,
           "wechat": _probe_wechat,
           "xiaohongshu": _probe_xiaohongshu}


def probe(cdp, platform, **kw):
    """拉两侧列表。返回 dict，判据见模块 docstring。"""
    plat = (platform or "").strip().lower()
    fn = _PROBES.get(plat)
    if fn is None:
        raise ValueError("未知平台 %r，可选 %s"
                         % (platform, "/".join(sorted(_PROBES))))
    return fn(cdp, **kw) if plat == "wechat" else fn(cdp)


# ---------------------------------------------------------------- 判据

def verdict_for(p, cid, case=None, title_maker=None):
    """从探针结果取某条 case 的本质结论。返回 (verdict, detail)。"""
    if not p:
        return "UNKNOWN", "对账没跑成（异常），重跑本命令即可"

    # 公众号特例：wechat_sync 已产出 diffs，直接按 code 判（口径更细，
    # 里面还区分了「群发」与「远端删除」的历史成因）。
    if p.get("diffs") is not None:
        for row in p["diffs"]:
            lvl, code, c, detail = (list(row) + [None] * 4)[:4]
            if c != cid:
                continue
            if code == "DRAFT_GONE_BUT_PUBLISHED":
                return "PUBLISHED", detail
            if code == "REMOTE_MISSING":
                return "MISSING", detail
        return "RATE_LIMIT", "草稿箱里还有这条 ⇒ 限流或记录级损坏"

    why = p.get("why") or ""
    if not (p.get("draft_ok") and p.get("pub_ok")):
        return "UNKNOWN", why or "任一侧接口没读到 ⇒ 无法区分「真没了」和「没读到」"

    case = case if isinstance(case, dict) else {"id": cid}
    d_hit = _match_local(cid, case, p.get("draft_titles") or [],
                         title_maker)
    if d_hit:
        return "RATE_LIMIT", "草稿箱里还有这条（标题命中「%s」）⇒ 限流或记录级损坏" % d_hit
    p_hit = _match_local(cid, case, p.get("pub_titles") or [], title_maker)
    if p_hit:
        return "PUBLISHED", ("草稿箱无 + 已发表列表有同标题「%s」⇒ 你后台群发过"
                             % p_hit)
    return "MISSING", "两侧接口都读成功、且都没有同标题 ⇒ 草稿真的没了（死篇号）"


def explain(platform, cid, case, p, title_maker=None):
    """人话版结论（直接 print 给用户看）。"""
    v, detail = verdict_for(p, cid, case, title_maker)
    plat = {"wechat": "公众号", "toutiao": "头条", "bilibili": "B站",
            "xiaohongshu": "小红书"}.get((platform or "").lower(), platform)
    lines = ["[%s] %s 定性：%s" % (plat, cid, v), "  判据：%s" % detail,
             "  下一步：%s" % NEXT_ACTION.get(v, "")]
    return "\n".join(lines)


def main(argv=None):
    """`python reconcile_verdict.py <platform> [--case CID] [--json]`"""
    import argparse
    import json
    ap = argparse.ArgumentParser(description="跨平台发布失败四态诊断")
    ap.add_argument("platform", help="/".join(sorted(_PROBES)))
    ap.add_argument("--case", default="", help="只看某条 case")
    ap.add_argument("--json", action="store_true", help="输出原始探针 JSON")
    args = ap.parse_args(argv)

    sys.path.insert(0, SCRIPTS)
    ports = {"toutiao": 9223, "bilibili": 9224, "wechat": 9222}
    port = ports.get(args.platform)
    if not port:
        print("小红书无服务端草稿 API，恒 UNKNOWN：%s" % _probe_xiaohongshu(None)["why"])
        return 2

    plat_cfg = None
    try:
        if args.platform == "toutiao":
            import toutiao_publish as tt
            plat_cfg = (tt, tt.TT_HOME, tt.CDP_PORT, tt.load_cases)
        elif args.platform == "bilibili":
            import bilibili_publish as bp
            plat_cfg = (bp, bp.HOME if hasattr(bp, "HOME") else
                        "https://member.bilibili.com",
                        bp.PUB_PORT, bp.load_cases)
        else:
            import wechat_publish as wp
            plat_cfg = (wp, "https://mp.weixin.qq.com", 9222, wp.load_cases)
    except Exception as e:                                  # noqa: BLE001
        print("导入平台模块失败：%s" % e)
        return 2

    mod, url, port, _load_cases = plat_cfg
    wp._autostart_chrome_if_needed(port, profile=_L.PLAT_PROFILE)
    if not wp._port_alive(port):
        print("端口 %d 不通（Chrome 没起来）" % port)
        return 2
    box = wp.CDP(port)
    tid = box.new_target(url)
    box.connect_target(tid)
    try:
        p = probe(box, args.platform)
        if args.json:
            print(json.dumps(p, ensure_ascii=False, indent=2))
            return 0
        if args.case:
            c = None
            try:
                cases = _load_cases()
                c = next((x for x in cases if x.get("id") == args.case), None)
            except Exception:                              # noqa: BLE001
                c = None
            print(explain(args.platform, args.case, c or {"id": args.case}, p))
            return 0 if verdict_for(p, args.case, c or {})[0] != "UNKNOWN" else 1
        print("草稿箱读到：%s（%d 条）" % (p.get("draft_ok"), len(p.get("draft_titles") or [])))
        print("已发表读到：%s（%d 条）" % (p.get("pub_ok"), len(p.get("pub_titles") or [])))
        if p.get("why"):
            print("注意：%s" % p["why"])
        return 0
    finally:
        box.close_target(tid)


if __name__ == "__main__":
    sys.exit(main())
