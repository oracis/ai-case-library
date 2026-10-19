"""修B站草稿标题：**原样回传 paragraphs 与 image_urls，只换标题**。

## 为什么不用 build_arg()

`build_arg()` 是从编辑器 HTML 重建段落的，会**丢行内样式**（粗体/颜色/居中）。
而 `draft/view` 返回的 `opus.content.paragraphs` 就是编辑器自己的结构，
**自带 `word.style`**（实测 43 段里 25 个节点带样式，含 `bold:true`）。
所以改标题时必须原样回传 paragraphs，只动 `title`。

## 为什么不是 `arg = dict(draft.get("arg") or {})`

`draft/view` **没有 `arg` 字段** → 空 dict → 保存时不带 `article_id`
→ **变成新建副本、空正文、无封面**，旧稿还在。2026-10-02 就是这么把
远端从 38 条炸到 101 条的。本脚本的 `_build_arg()` 显式列出必填字段。

用法：
    python -u -X utf8 scripts/bili_title_fix.py --plan
    python -u -X utf8 scripts/bili_title_fix.py --run --batch 5
    python -u -X utf8 scripts/bili_title_fix.py --check
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili_title_push as tp        # noqa: E402
import bili_draft_api as api         # noqa: E402

OUT = os.path.join(tp.bp.ROOT, "out", "bili_title_fix.json")
STATE = os.path.join(tp.bp.ROOT, "out", "bili_cover_state.json")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {} if default is None else default


def save(path, d):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def _build_arg(draft, want):
    """从 draft/view 的**扁平结构**重建 draft/add要的嵌套 arg。

    ⚠⚠三条铁律（2026-10-02 踩出来的）：
      1. 必须带 `article_id` —— 不带就是新建副本（101 条事故的根因）。
      2. 必须带 `image_urls` —— 不带封面就没了（新建请求没有封面）。
      3. `opus.content` 要传**结构化 paragraphs**，不是 view 里那个
         `content` 纯文本字符串；原样回传即可保住行内样式。
    标题要同时写 `arg["title"]` 和 `arg["opus"]["title"]` —— 只改一处会被
    另一个覆盖回去（实测 list 摘要显示的还是旧标题）。
    """
    opus = draft.get("opus") or {}
    arg = {
        "type": draft.get("type") or 4,
        "template_id": opus.get("template_id") or draft.get("template_id") or 1,
        "category_id": (draft.get("category") or {}).get("id")
                       or draft.get("category_id") or 15,
        "title": want,
        "private_pub": draft.get("private_pub", 2),
        "reprint": 1 if draft.get("reprint") else 0,
        "original": 1 if draft.get("original") else 0,
        "list_id": (draft.get("list") or {}).get("id") or 0,
        "comment_selected": 1 if draft.get("comment_selected") else 0,
        "up_closed_reply": 1 if draft.get("up_closed_reply") else 0,
        "timer_pub_time": draft.get("timer_pub_time") or 0,
        "only_fans_level": draft.get("only_fans_level") or 0,
        "only_fans_dnd": draft.get("only_fans_dnd") or 0,
        "summary": draft.get("summary") or "",
        "opus": {
            "opus_source": opus.get("opus_source", 2),
            "title": want,
            "content": opus.get("content") or {"paragraphs": []},
            "pub_info": opus.get("pub_info") or {},
            "attachments": opus.get("attachments") or {"is_aigc": 0},
        },
        "article_id": draft["article_id"],
    }
    iu = draft.get("image_urls") or draft.get("origin_image_urls") or []
    if iu:
        arg["image_urls"] = list(iu)[:1]      # B站只认首图作封面
    return arg


def plan():
    """列出待修：有正文+封面的旧标题草稿 → 新标题。"""
    st = load(STATE)
    ov = tp.bp._bili_title_overrides()
    todo = []
    for aid, v in st.items():
        cid = v.get("cid") or ""
        want = ov.get(cid)
        if not want or v.get("err"):
            continue
        cur = (v.get("title") or "").strip()
        if cur == want:
            continue
        # 只修「有正文 + 有封面」的那条；空壳（paras=0/无封面）跳过 ——
        # 那是事故副本，等这边改完标题后按同标题规则删掉。
        if not v.get("paras") or not v.get("cover"):
            continue
        todo.append({"cid": cid, "aid": int(aid), "from": cur, "to": want,
                     "paras": v["paras"], "cover": v["cover"]})
    todo.sort(key=lambda x: x["cid"])
    print("远端 %d 条｜需改标题 %d 条" % (len(st), len(todo)))
    print("-" * 78)
    for t in todo:
        print("%-22s aid=%-8d %2d段\n    旧: %s\n    新: %s"
              % (t["cid"], t["aid"], t["paras"], t["from"], t["to"]))
    return todo


def run(batch=5):
    todo = plan()
    if not todo:
        print("没有要改的")
        return 0
    st = load(OUT)
    # ⚠⚠ 必须**先滤掉已完成的再切片**。否则 `todo[:batch]` 永远取到最前面那
    # 几条，它们已经在台账里 ok → 全部 continue → 第二轮开始每轮都是空转，
    # 表现为「跑了很多轮只改了 5 条」（2026-10-02 踩过）。
    todo = [t for t in todo if not st.get(t["cid"], {}).get("ok")]
    if not todo:
        print("没有要改的（全部已完成）")
        return 0
    batch = min(batch, len(todo))
    cdp = None
    done = fail = 0
    for i, t in enumerate(todo[:batch], 1):
        for attempt in range(3):
            try:
                if cdp is None:
                    cdp = tp.connect()
                draft = api.view_draft(cdp, t["aid"])
                paras = len(((draft.get("opus") or {}).get("content") or {})
                            .get("paragraphs") or [])
                iu = draft.get("image_urls") or []
                if not paras or not iu:
                    raise RuntimeError("源草稿已不完整 paras=%d封面=%d"
                                       % (paras, len(iu)))
                arg = _build_arg(draft, t["to"])
                r = api.save_draft(cdp, arg)
                if r.get("code") != 0:
                    raise RuntimeError("draft/add code=%s %s"
                                       % (r.get("code"), r.get("message")))
                # ⚠ code=0 只代表请求合法，**必须回读验证**
                time.sleep(2)
                if cdp is None:
                    cdp = tp.connect()
                v = api.view_draft(cdp, t["aid"])
                real = (v.get("title") or "").strip()
                otitle = ((v.get("opus") or {}).get("title") or "").strip()
                rp = len(((v.get("opus") or {}).get("content") or {})
                         .get("paragraphs") or [])
                riu = v.get("image_urls") or []
                if real != t["to"]:
                    raise RuntimeError("回读标题=%r（期望 %r）" % (real, t["to"]))
                if not rp or not riu:
                    raise RuntimeError("回读正文/封面丢了 paras=%d 封面=%d"
                                       % (rp, len(riu)))
                st[t["cid"]] = {"ok": True, "aid": t["aid"],
                                "from": t["from"], "to": real,
                                "paras": rp, "cover": riu[0],
                                "opus_title": otitle,
                                "at": time.strftime("%H:%M:%S")}
                done += 1
                print("[%d/%d] ✓ %-22s aid=%d %d段 %s"
                      % (i, min(batch, len(todo)), t["cid"], t["aid"], rp,
                         "opus.title同步" if otitle == real else "⚠opus.title不一致"),
                      flush=True)
                break
            except Exception as exc:                   # noqa: BLE001
                msg = str(exc)
                if "10061" in msg or "10054" in msg or "Bad" in msg \
                        or "10053" in msg:
                    cdp = None
                    try:
                        import chrome_cdp_launch as cl
                        import wechat_publish as wp
                        import bilibili_publish as bp
                        wp._autostart_chrome_if_needed(
                            bp.PUB_PORT, profile=cl.PLAT_PROFILE)
                    except Exception:                  # noqa: BLE001
                        pass
                    time.sleep(3)
                    continue
                st[t["cid"]] = {"ok": False, "aid": t["aid"], "err": msg[:120],
                                "at": time.strftime("%H:%M:%S")}
                fail += 1
                print("[%d] ✗ %-22s %s" % (i, t["cid"], msg[:90]), flush=True)
                break
        save(OUT, st)
    save(OUT, st)
    print("=" * 60)
    print("本次成功 %d ｜ 失败 %d ｜ 台账累计 %d" % (done, fail, len(st)))
    return 0


def check():
    """全量回读验真：标题 / 段落数 / 封面。"""
    cdp = tp.connect()
    ds = api.list_drafts(cdp, pn=1, ps=200)
    ov = tp.bp._bili_title_overrides()
    res = {}
    for i, d in enumerate(ds, 1):
        aid = d.get("article_id")
        t = (d.get("title") or "").strip()
        cid = tp._cid_by_title_any(t) or ("unknown:%s" % aid)
        v = api.view_draft(cdp, aid)
        paras = len(((v.get("opus") or {}).get("content") or {})
                    .get("paragraphs") or [])
        iu = v.get("image_urls") or []
        res[str(aid)] = {"cid": cid, "aid": aid, "title": t, "paras": paras,
                         "cover": iu[0] if iu else None}
        print("[%d/%d] %-22s %2d段 %s"
              % (i, len(ds), cid, paras, "有封面" if iu else "⚠无封面"),
              flush=True)
    save(STATE, res)
    # 按 cid 汇总
    by = {}
    for v in res.values():
        by.setdefault(v["cid"], []).append(v)
    dup = {c: vs for c, vs in by.items() if len(vs) > 1}
    empty = [c for c, vs in by.items()
             if all(not x["paras"] or not x["cover"] for x in vs)]
    bad_title = []
    for cid, vs in by.items():
        w = ov.get(cid)
        if not w:
            continue
        for v in vs:
            if v["title"] != w and v["paras"]:
                bad_title.append((cid, v["aid"], v["title"], w))
    print("=" * 68)
    print("远端 %d 条 ｜ case %d ｜ 每case>1条 %d ｜ 全空壳case %d"
          % (len(res), len(by), len(dup), len(empty)))
    print("有效草稿（有正文有封面）标题不等于 overrides 的：%d" % len(bad_title))
    for cid, aid, got, want in bad_title[:10]:
        print("  %-22s aid=%d\n    远端: %s\n    期望: %s" % (cid, aid, got, want))
    print("结果已存", STATE)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--batch", type=int, default=5)
    a = ap.parse_args()
    if a.plan:
        plan()
    elif a.run:
        run(a.batch)
    elif a.check:
        check()
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
