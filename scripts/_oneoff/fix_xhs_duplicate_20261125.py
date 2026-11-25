"""清小红书重复稿后的台账收尾（2026-11-25）。

背景
----
ai-prank-call 2026-10-26 已在小红书发过一条（note 6adefddb0000000015004eb5），
11-25 08:18 因 FIFO 标题没对上案例被又发了一条（6b06296f000000002b01bfe3），
线上出现同名双笔记。用户裁定「清重复稿」：**删掉 11-25 误发的新笔记，保留 10-26 原笔记**。

`xhs_delete.py --apply` 只会「把指向被删笔记的那条台账移除」，所以现在：
  · `data/xhs_published.json` 里 ai-prank-call 整条消失（49 条），
  · `platform_posts` 里 xhs/ai-prank-call 行也没了 —— 而旧笔记其实还在线上。
⇒ 本脚本做两件收尾：
  1) 依据线上真值（旧笔记 note_id + 平台时间）把 ai-prank-call 补回两本账；
  2) 把已发表的 verse 从 `xhs_drafts.json`（草稿账）里摘掉，
     消除 ledger_health 报的「同一个 case 同时登记在两本账」。

红线：db 只走单条 INSERT/UPDATE；JSON 只动这两条记录，其余原样；写完逐项回读。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

PUB = os.path.join(ROOT, "data", "xhs_published.json")
DRAFT = os.path.join(ROOT, "data", "xhs_drafts.json")

OLD_NOTE = "6adefddb0000000015004eb5"        # 10-26 原笔记（线上仍在）
OLD_AT = "2026-10-26 15:14"                  # 平台列表时间
OLD_PUBLISHED_AT = "2026-10-26 15:28"        # SQLite 快照时间，按 #104 裁定不动
OLD_TITLE = "用名人声音打恶作剧电话，毛利 95%"
ROW = {
    "id": "ai-prank-call",
    "at": OLD_AT,
    "note_id": OLD_NOTE,
    "title": OLD_TITLE,
    "evidence": "online_readback_20261125",
    "note": ("11-25 清重复稿：误发的 6b06296f000000002b01bfe3（08:18）已在平台删除并回读确认消失，"
             "台账按线上唯一剩余条目回指 10-26 原笔记；published_at 快照 %s 不动" % OLD_PUBLISHED_AT),
}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, obj, trailing_newline):
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if trailing_newline:
        text += "\n"
    with atomicio.file_lock(path + ".lock"):
        atomicio.write_text(path, text)


def main():
    apply = "--yes" in sys.argv

    pub = _read(PUB)
    drafts = _read(DRAFT)
    pub_before, draft_before = len(pub), len(drafts)

    pub_new = [r for r in pub if r.get("id") != "ai-prank-call"]
    idx = next((i for i, r in enumerate(pub_new)
                if str(r.get("at") or "") > OLD_AT), len(pub_new))
    pub_new.insert(idx, dict(ROW))

    drafts_new = [r for r in drafts if r.get("id") != "verse"]

    print("published: %d → %d（插入 index=%d）" % (pub_before, len(pub_new), idx))
    print("drafts   : %d → %d（摘掉 verse）" % (draft_before, len(drafts_new)))
    lost = [r.get("id") for r in pub if r.get("id") not in {x.get("id") for x in pub_new}]
    print("published 丢条目:", lost or "无")

    if not apply:
        print("（干跑）确认无误后加 --yes")
        return 0

    _write(PUB, pub_new, trailing_newline=True)
    _write(DRAFT, drafts_new, trailing_newline=False)

    # ---- db：单条 INSERT（行已不存在）----
    import db  # noqa: E402
    conn = db.get_conn()
    cur = conn.cursor()
    cur.execute("select count(*) from platform_posts where platform='xhs' and case_id='ai-prank-call'")
    existed = cur.fetchone()[0]
    if existed:
        cur.execute(
            "update platform_posts set state='published', remote_id=?, title=?, published_at=?, "
            "updated_at=? where platform='xhs' and case_id='ai-prank-call'", 
            (OLD_NOTE, OLD_TITLE, OLD_PUBLISHED_AT, OLD_AT))
        print("db: UPDATE 影响行数", cur.rowcount)
    else:
        cur.execute(
            "insert into platform_posts (case_id, platform, state, remote_id, title, published_at, "
            "updated_at, note) values ('ai-prank-call','xhs','published',?,?,?,?,?)",
            (OLD_NOTE, OLD_TITLE, OLD_PUBLISHED_AT, OLD_AT, "11-25 清重复稿后回指 10-26 原笔记"))
        print("db: INSERT 影响行数", cur.rowcount)
    conn.commit()

    # ---- 回读 ----
    back_pub = _read(PUB)
    back_draft = _read(DRAFT)
    got = [r for r in back_pub if r.get("id") == "ai-prank-call"]
    print("回读 published:", json.dumps(got, ensure_ascii=False))
    print("回读 drafts 含 verse?", any(r.get("id") == "verse" for r in back_draft))
    cur.execute("select case_id,state,remote_id,published_at from platform_posts "
                "where platform='xhs' and case_id in ('ai-prank-call','verse')")
    for r in cur.fetchall():
        print("回读 db:", dict(r))
    ok = (len(got) == 1 and got[0].get("note_id") == OLD_NOTE
          and not any(r.get("id") == "verse" for r in back_draft))
    print("✓ 收尾完成" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
