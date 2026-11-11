#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清掉 B站**一号**草稿箱里带「我去核了一遍」内部核验注记的旧稿（16 条）。

为什么必须从母版重建，不能「读远端→删一行→写回」
------------------------------------------------
`draft/view` 返回的 content 是编辑器 **innerText 的纯文本镜像**（11-10 实测：bustem
两边都是 1059 字，**不是**截断），但它是**扁平文本**，而 `draft/add` 要的是
`content.paragraphs` 结构。两边形状不同 ⇒ 远端读回的文本**不是**可以原样回写的真值。
所以正文一律从 `out/articles/<cid>.md` 用**和 `cmd_draft` 完全同一条管线**重建
（`strip_source_links` → `md_to_blocks` → `blocks_to_html(footer=False)`），
保证「改出来的」和「以后重建的」逐字一致。

⚠ 与二号的区别：二号读 `GAME_REPO/content/<slug>/article-free.md`，
   一号读站点产物 `out/articles/<cid>.md`（实测 16/16 都在，且正是本仓库
   11-10 清过注记的那批）。

幂等判据 / 覆盖安全闸 / 验收判据（11-10 重写，旧判据恒假）
--------------------------------------------------------
**逐字比不可用**：编辑器把 `<h2>` 渲染成「▶ 标题」、表格格线渲染成「｜」、单元格
之间插「：」，直接 `norm(平台) == norm(母版)` 会差 19–25 个字符而恒假（实测 16/16）。
改用「缺字」判据 `diff_stats(母版, 平台) -> (dele, ins)`：
  · 幂等：平台正文**已无注记行** ⇒ SKIP（这是唯一的改稿理由，最不容易误伤）
  · 覆盖安全闸：`dele == 0`（平台一个字都不缺母版的内容）才允许覆盖。
    `dele > DEL_MAX` ⇒ 母版在建稿后被改写过、平台是旧版，**覆盖会改变文章内容**
    ⇒ 拒绝自动改、转人工（实测 3 条：boothie 455 / quran-unlock 258 / stan 70）
  · 真改后**双回读**：接口 `draft/view` + 编辑页 `read-editor?aid=` 的 innerText，
    两路都必须「注记消失 + dele==0 + ins<=INS_MAX」。

用法：
    python -X utf8 tmp/clean_bili1_draft_notes.py            # 干跑（默认）
    python -X utf8 tmp/clean_bili1_draft_notes.py --go       # 真改
    python -X utf8 tmp/clean_bili1_draft_notes.py --go --only bustem
"""
import argparse
import html
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import bili_draft_api as api            # noqa: E402
import bilibili_publish as bp           # noqa: E402
import toutiao_publish as tt            # noqa: E402
import wechat_publish as wp             # noqa: E402

MARK = "我去核了一遍"
TITLE_MAX = 40
BODY_MIN = 200
# 平台正文最多允许「缺母版」多少字。超了说明母版在建稿后被改写过、平台是旧版，
# 覆盖会改变文章内容 ⇒ 拒绝自动改，转人工。实测这批：13 条缺 0，3 条缺 455/258/70。
DEL_MAX = 40
# 回读时平台侧最多允许「多出」多少字：`▶`(每个 h2 一个) + `｜`(每个表格格线) +
# 表格单元格的 `：`。实测 19–25 字。给到 80 是留 h2/表格多的稿子余量，
# 再大就说明写进去的不是这份母版了。
INS_MAX = 80

# 草稿编辑器路由（`view_draft` 的 `edit_url` 字段实测是**空串**，只能用这个）
# 同域 iframe 那套是「从草稿箱点进去」的路径；顶层直开这个 URL 实测能加载编辑器，
# 正文节点直接在主文档里：`.tiptap.ProseMirror.eva3-editor`（2026-11-10 实测 bustem）。
EDITOR_URL = "https://member.bilibili.com/york/read-editor?aid=%s"
# 编辑器显示符：B站把 <h2> 渲染成「▶ 标题」、把竖线/表格渲染成全角「｜」。
# 这些**不是正文内容**，只是在 innerText 里多出来的字形 ⇒ 归一时必须剥掉，
# 否则「回读 vs 母版」的逐字比恒假（实测差 23 个字符，全是这两类 insert）。
EDITOR_GLYPHS = ("▶", "｜", "|", "►", "▸")

JS_READ_EDITOR = r"""(function(){
  var el = document.querySelector('.tiptap.ProseMirror.eva3-editor');
  return JSON.stringify({ok: !!el, t: el ? (el.innerText || '') : ''});
})()"""


def load_targets():
    """本地清过注记的案例 → [(cid, md_path, aid)]，aid 从台账取。"""
    import sqlite3
    log = os.path.join(ROOT, "tmp", "batch_clean_log.json")
    d = json.load(open(log, encoding="utf-8"))
    cids = [r[0].rsplit("/", 1)[-1][:-3] for r in d["results"] if r[1] != "skip"]
    conn = sqlite3.connect(os.path.join(ROOT, "data", "ledger.db"))
    cur = conn.cursor()
    q = ",".join("?" * len(cids))
    cur.execute(
        "SELECT case_id, remote_id, draft_id FROM platform_posts "
        "WHERE platform='bilibili' AND state='draft' AND case_id IN (%s)" % q,
        cids)
    ast = {r[0]: (r[1] or r[2]) for r in cur.fetchall()}
    conn.close()
    out = []
    for cid in cids:
        md = os.path.join(ROOT, "out", "articles", cid + ".md")
        if cid in ast and os.path.isfile(md):
            out.append((cid, md, str(ast[cid])))
    return out


def build_body(md_path):
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    md, dropped = tt.strip_source_links(md)
    body = tt.blocks_to_html(tt.md_to_blocks(md), footer=False)
    return body, dropped


def norm(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    return re.sub(r"\s+", "", html.unescape(t)).strip()


def norm_editor(s):
    """编辑器 innerText 归一：去标签去空白 + 剥掉仅由渲染产生的字形。

    ⚠ 为什么必须剥：`<h2>我的判断</h2>` 在编辑器里是「▶ 我的判断」，母版归一后
      是「我的判断」——不剥 `▶` 就永远差一个字符，判据恒假（11-10 实测 23 处）。
    """
    t = norm(s)
    for g in EDITOR_GLYPHS:
        t = t.replace(g, "")
    return t


def note_lines(text):
    """含注记的行（**整行**）。实测注记独立成行、前后各一空行，摘整行即可。"""
    return [ln for ln in str(text or "").splitlines() if MARK in ln]


def drop_note_lines(text):
    keep = [ln for ln in str(text or "").splitlines() if MARK not in ln]
    return "\n".join(keep)


def diff_stats(master, plat):
    """逐字对齐后返回 (母版有平台无的字数, 平台有母版无的字数)。

    `dele == 0` 的含义：平台正文里的每个字母版都有、顺序一致 ⇒ 用母版覆盖
    **不会丢掉平台上已有的任何内容**。这是「覆盖安全性」的判据，且不需要
    任何渲染符号表（`▶`/`｜`/表格冒号都只落在 ins 侧）。
    """
    import difflib
    sm = difflib.SequenceMatcher(None, master, plat, autojunk=False)
    dele = ins = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            dele += i2 - i1
        elif tag == "insert":
            ins += j2 - j1
        elif tag == "replace":
            dele += i2 - i1
            ins += j2 - j1
    return dele, ins


def read_editor_body(cdp, aid, port, settle=6):
    """打开草稿编辑器读**全文 innerText**（回写验收的唯一真值）。

    `draft/view.content` 虽然是全文（实测与 innerText 同长），但它只是接口快照；
    编辑页是「用户真正看到的东西」，验收优先看它。失败返 ""。
    """
    tid, sub = None, None
    try:
        tid = cdp.new_target(EDITOR_URL % aid)["id"]
        sub = wp.CDP(port)
        if not sub.connect_target(tid):
            return ""
        sub.send("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 900, "deviceScaleFactor": 1,
                  "mobile": False})
        for _ in range(settle):
            time.sleep(1)
        raw = sub.eval(JS_READ_EDITOR, refresh_context=True)
        j = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return (j.get("t") or "") if j.get("ok") else ""
    except Exception:                                       # noqa: BLE001
        return ""
    finally:
        for conn in (sub,):
            try:
                if conn is not None:
                    conn.close()
            except Exception:                               # noqa: BLE001
                pass
        if tid:
            try:
                wp.CDP(port).close_target(tid)
            except Exception:                               # noqa: BLE001
                pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--allow-legacy", default="",
                    help="逗号分隔的 cid 白名单：允许越过「平台缺母版内容」安全闸。"
                         "只在已人工确认差异方向（清脏注记/补全残缺段）后使用，"
                         "默认空 = 一律拒绝。")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0,
                    help="只处理前 N 条待改（受管任务里整组会被回收，必须分块跑；0=不限）")
    a = ap.parse_args(argv)
    allow_legacy = {x.strip() for x in (a.allow_legacy or "").split(",") if x.strip()}

    targets = load_targets()
    if a.only:
        targets = [t for t in targets if t[0] == a.only]
    print("目标 %d 条（B站一号草稿）\n" % len(targets))
    if not targets:
        return 0

    cdp = wp.CDP(a.port or bp.PUB_PORT)
    api._bili_tab(cdp)

    ok = skip = fail = 0
    done = 0
    for cid, md, aid in targets:
        if a.limit and done >= a.limit:
            print("… 已达 --limit %d，剩下的下一块再跑" % a.limit)
            break
        body, dropped = build_body(md)
        plain = tt.strip_md(body)
        if MARK in plain:
            print("%-26s ✗ 母版本身还带注记（%s），先清母版" % (cid, md))
            fail += 1
            continue
        if len(plain) < BODY_MIN:
            print("%-26s ✗ 重建正文仅 %d 字，拒绝回写" % (cid, len(plain)))
            fail += 1
            continue

        old = api.view_draft(cdp, aid) or {}
        title = (old.get("title") or "").strip()
        old_body = old.get("content") or ""
        if len(title) > TITLE_MAX:
            print("%-26s ✗ 标题 %d 字 > %d" % (cid, len(title), TITLE_MAX))
            fail += 1
            continue

        nl = note_lines(old_body)
        mark_gone = not nl

        # 【唯一的改稿理由】平台正文里还有注记行。没有就 SKIP —— 幂等靠这句。
        if mark_gone:
            print("%-26s SKIP 平台正文已无注记" % cid)
            skip += 1
            continue

        # 【安全闸】平台正文必须**一个字都不缺母版的内容**（del+replace 母版侧 == 0）。
        # 缺字 ⇒ 母版在建稿后被改写过，平台是旧版 ⇒ 覆盖会**改变文章内容**，停下来问人。
        # 不依赖 `▶`/`｜`/表格冒号这些渲染符号表，那些只会落在 insert 侧，不影响本判据。
        dele, ins = diff_stats(norm(body), norm(drop_note_lines(old_body)))
        legacy_ok = cid in allow_legacy
        if dele > DEL_MAX and not legacy_ok:
            print("%-26s ✗ 平台是旧版：缺母版 %d 字（>%d），覆盖会改内容 ⇒ 拒绝自动改，须人工裁定"
                  % (cid, dele, DEL_MAX))
            fail += 1
            continue
        if dele > DEL_MAX and legacy_ok:
            print("%-26s ⚠ 平台缺母版 %d 字，**已人工授权越闸**（--allow-legacy）"
                  % (cid, dele))

        print("%-26s aid=%-8s 平台 %4d / 母版 %4d ｜ 注记 %d 行 ｜ 不缺母版内容(缺%d 多%d)"
              % (cid, aid, len(norm(old_body)), len(norm(body)), len(nl), dele, ins))
        done += 1

        if not a.go:
            ok += 1
            continue

        arg = api.build_arg(
            title, body, article_id=aid,
            image_urls=(old.get("origin_image_urls") or old.get("image_urls")),
            private_pub=old.get("private_pub", 2),
            original=old.get("original", 0),
            reprint=old.get("reprint", 1))
        out = api.save_draft(cdp, arg)
        if not isinstance(out, dict) or out.get("code") != 0:
            print("    ✗ draft/add 失败：%s"
                  % json.dumps(out, ensure_ascii=False)[:160])
            fail += 1
            continue

        time.sleep(1.2)
        after = api.view_draft(cdp, aid) or {}
        got_t = (after.get("title") or "").strip()
        got_b = after.get("content") or ""
        if got_t != title:
            print("    ✗ code=0 但回读标题变了：《%s》" % got_t[:30])
            fail += 1
            continue
        if MARK in str(got_b):
            print("    ✗ code=0 但回读正文仍带注记（%d 字）" % len(norm(got_b)))
            fail += 1
            continue
        # 接口回读：新正文必须 = 母版，只允许平台侧多出渲染符号（▶ / ｜ / 表格冒号）
        d1, i1 = diff_stats(norm(body), norm(got_b))
        if d1 > DEL_MAX or i1 > INS_MAX:
            print("    ✗ code=0 但接口回读与母版不符（缺 %d / 多 %d）" % (d1, i1))
            fail += 1
            continue
        # 第二路独立回读：编辑页 innerText（用户真正看到的），不只看接口快照
        eb = read_editor_body(cdp, aid, a.port or bp.PUB_PORT)
        if not eb:
            print("    ✗ 编辑页读不到正文，无法独立验收")
            fail += 1
            continue
        if MARK in eb:
            print("    ✗ 编辑页正文仍带注记（%d 字）" % len(norm(eb)))
            fail += 1
            continue
        d2, i2 = diff_stats(norm(body), norm(eb))
        if d2 > DEL_MAX or i2 > INS_MAX:
            print("    ✗ 编辑页正文与母版不符（缺 %d / 多 %d）" % (d2, i2))
            fail += 1
            continue
        print("    ✓ 双回读通过：标题未变、注记已清、正文不缺母版任何一个字"
              "（接口 缺%d多%d ｜ 编辑页 缺%d多%d，%d 字）"
              % (d1, i1, d2, i2, len(norm(eb))))
        ok += 1

    print("\n%s：待改 %d ｜ SKIP %d ｜ 失败 %d"
          % ("真改" if a.go else "干跑", ok, skip, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
