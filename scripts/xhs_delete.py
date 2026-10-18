#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书**删除已发布笔记**（纯接口，2026-10-18 打通）。

## 为什么这条链路能通、发布却不通

两个网关、两套风控，别混为一谈：

| 动作 | 端点 | 结果 |
|---|---|---|
| 创建笔记 | `edith /web_api/sns/v2/note` | ❌ HTTP 461 + `verifytype:124`（见 probe_xhs_publish_api.py） |
| **删除笔记** | `edith /web_api/sns/capa/postgw/note/delete` | ✅ HTTP 200，code 0 |

`capa/postgw` 是另一个服务，删除不在发布那道风控后面。所以「发布卡死」不能
外推成「删除也不行」—— 这是当初没试就搁置的误判。

## 参数是问出来的

`{"noteIds": [...]}` → 400 `required param check: note_id`
`{"note_id": "..."}` → 200，假 ID 回 `code:100601 根据笔记ID列表批量查询笔记基础信息失败`
⇒ 字段名是**单数** `note_id`，值可直接给字符串。

## 安全设计

- **默认 dry-run**：只打印「将删哪条 + 影响哪些台账条目」，不发删除请求。
- `--apply` 才真删，顺序是 **快照 → 删除 → 回读验证 → 台账同步**。
- 快照写 `data/xhs_deleted.json`（含 posted 返回的完整字段 + 图片 URL + 当时数据），
  线上删掉后本地仍能复现内容（正文/卡片图另在 `out/xhs/<case>/`）。
- 台账 `data/xhs_published.json` 只保留**线上真实存在**的条目，先 `.bak-<时间戳>`。

## 用法

    python -X utf8 scripts/xhs_delete.py --note-id 6abbc7d8...            # 预演
    python -X utf8 scripts/xhs_delete.py --note-id 6abbc7d8... --apply    # 执行
    python -X utf8 scripts/xhs_delete.py --dup-of prosp                   # 列出该 case 的重复条目
"""
import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xhs_webapi as X  # noqa: E402

ROOT = os.path.dirname(HERE)
LEDGER = os.path.join(ROOT, "data", "xhs_published.json")
ARCHIVE = os.path.join(ROOT, "data", "xhs_deleted.json")
DELETE_URL = "https://edith.xiaohongshu.com/web_api/sns/capa/postgw/note/delete"

# 风控/异常状态码（与 publish 探测保持同一套口径）
NEED_VERIFY = (460, 461, 471)


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2))
        f.write("\n")


def find_note(notes, note_id):
    for n in notes:
        if n.get("note_id") == note_id:
            return n
    return None


def ledger_hits(note_id):
    """台账里引用该 note_id 的条目（返回 [(index, entry)]）。"""
    rows = _read_json(LEDGER, [])
    if not isinstance(rows, list):
        return []
    return [(i, e) for i, e in enumerate(rows) if e.get("note_id") == note_id]


def delete_note(cdp, note_id):
    """调删除接口，返回 dict(status, code, msg, verifytype, raw)。"""
    st, js, raw, hs = X.fetch_full(cdp, DELETE_URL, "POST", {"note_id": note_id})
    return {"status": st, "code": (js or {}).get("code"),
            "msg": str((js or {}).get("msg") or ""), "raw": raw[:300],
            "verifytype": hs.get("verifytype") or hs.get("Verifytype") or "",
            "json": js}


def archive(note, note_id, applied):
    """把被删笔记的完整快照追加到 data/xhs_deleted.json。"""
    rows = _read_json(ARCHIVE, [])
    if not isinstance(rows, list):
        rows = []
    rows.append({"note_id": note_id, "deleted_at": time.strftime("%Y-%m-%d %H:%M"),
                 "applied": bool(applied), "snapshot": note})
    _write_json(ARCHIVE, rows)
    return len(rows)


def drop_from_ledger(note_id):
    """从台账移除该 note_id 的条目（先备份），返回移除条数。"""
    rows = _read_json(LEDGER, [])
    if not isinstance(rows, list):
        return 0
    kept = [e for e in rows if e.get("note_id") != note_id]
    if len(kept) == len(rows):
        return 0
    shutil.copy2(LEDGER, "%s.bak-%s" % (LEDGER, time.strftime("%Y%m%d-%H%M%S")))
    _write_json(LEDGER, kept)
    return len(rows) - len(kept)


def cmd_dup(cdp, case_id):
    """列出某 case 在线上发过的所有条目（按时间排序，标出建议保留项）。"""
    notes, _ = X.posted_notes(pages=3, cdp=cdp)
    rows = sorted(notes, key=lambda n: n.get("time") or "")
    mine = [n for n in rows if n.get("title") and case_id.lower() in (n.get("title") or "").lower()]
    if not mine:
        print("线上没有标题含 %r 的笔记" % case_id)
        return 2
    print("标题含 %r 的线上笔记 %d 条：" % (case_id, len(mine)))
    for n in mine:
        print("  %s  %s  赞%-3s 看%-5s 藏%-3s 分享%-3s  %s"
              % (n.get("time"), n.get("note_id"), n.get("likes"), n.get("view_count"),
                 n.get("collected_count"), n.get("shared_count"), n.get("title", "")[:30]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note-id", help="要删除的笔记 ID")
    ap.add_argument("--dup-of", help="列出标题含该关键字的线上笔记（只读）")
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只预演）")
    ap.add_argument("--keep-ledger", action="store_true", help="删除后不动台账")
    args = ap.parse_args()

    if not args.note_id and not args.dup_of:
        ap.print_help()
        return 1

    cdp, page = X.connect()
    if cdp is None:
        print("✗ 连不上 9223（先 python scripts/_launch_9223.py --hold 28800）")
        return 1
    try:
        if args.dup_of:
            return cmd_dup(cdp, args.dup_of)

        notes, _ = X.posted_notes(pages=3, cdp=cdp)
        note = find_note(notes, args.note_id)
        if not note:
            print("✗ 线上找不到 %s（可能已删除，或 ID 拼错）" % args.note_id)
            return 2

        hits = ledger_hits(args.note_id)
        print("目标笔记")
        print("  note_id : %s" % args.note_id)
        print("  标题    : %s" % note.get("display_title") or note.get("title"))
        print("  发布    : %s" % note.get("time"))
        print("  数据    : 赞%s 看%s 藏%s 分享%s 评%s"
              % (note.get("likes"), note.get("view_count"), note.get("collected_count"),
                 note.get("shared_count"), note.get("comments_count")))
        print("  图片    : %d 张" % len(note.get("images_list") or []))
        print("  台账    : %s"
              % (", ".join("%s@%s" % (e.get("id"), e.get("at")) for _, e in hits) or "无条目"))

        if not args.apply:
            print("\n（预演，未发删除请求。确认无误后加 --apply）")
            return 0

        print("\n[1] 快照归档")
        n_arch = archive(note, args.note_id, applied=True)
        print("  ✓ data/xhs_deleted.json 现有 %d 条" % n_arch)

        print("[2] 调删除接口")
        r = delete_note(cdp, args.note_id)
        print("  HTTP %s  code=%s  verifytype=%s  msg=%s"
              % (r["status"], r["code"], r["verifytype"] or "-", r["msg"] or "-"))
        if r["status"] in NEED_VERIFY:
            print("  ✗ 触发风控（%s），删除未生效；快照已留，可用 DOM 兜底" % r["status"])
            return 3
        if r["status"] != 200 or r["code"] not in (0, None):
            print("  ✗ 删除被拒：%s" % (r["raw"] or r["msg"]))
            return 3

        print("[3] 回读验证")
        time.sleep(2)
        after, _ = X.posted_notes(pages=3, cdp=cdp)
        if find_note(after, args.note_id):
            # code 0 但列表里还在：可能是缓存/延迟，不能算删成功
            time.sleep(3)
            after, _ = X.posted_notes(pages=3, cdp=cdp)
        if find_note(after, args.note_id):
            print("  ⚠ 接口返回成功，但列表里仍能查到（可能延迟），稍后复查")
            print("  线上现在 %d 条" % len(after))
            return 4
        print("  ✓ 已从线上消失（%d → %d 条）" % (len(notes), len(after)))

        if not args.keep_ledger:
            print("[4] 台账同步")
            n = drop_from_ledger(args.note_id)
            print("  ✓ 移除 %d 条（备份 data/xhs_published.json.bak-*）" % n)
        print("\n完成。内容仍在 out/xhs/ 与 data/xhs_deleted.json，需要可重发。")
        return 0
    finally:
        try:
            cdp.close_target(cdp.tid)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
