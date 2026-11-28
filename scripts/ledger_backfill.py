#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""已发侧补账的安全通用入口（2026-11-28 由四个一次性脚本泛化）。

四个源脚本都是案号 / URL / 时间硬编码，不能直接复用到别的 case：
  · promote_wagiff_20261128.py        B站 draft→published（PROMOTE）
  · backfill_wx_freedoom_20261128.py  公众号线上已发、本地漏登（ORPHAN）
  · fix_xhs_staticrun_20261128.py     小红书草稿行残留 + note_id 缺失（DUP + NO_ID）
  · sim_bili_reconcile.py             模拟 L.save() 整本回写的字段洗（只读）

本工具把它们改成参数化子命令，并钉死四条安全线：
  1. 默认干跑，只有 --apply 才写盘；
  2. 目标已经处于期望状态 -> skip，不写；状态冲突 / 证据不足 -> refuse，不写；
  3. 写前备份 JSON + db，任一环节失败就用备份回滚（close db 之后再做）；
  4. published_at 必须显式传平台真实时点，绝不拿 now 冒充发表日。

用法：
    python scripts/ledger_backfill.py bili-promote --case <cid> --draft-id <六位> \
        --article-id <八位> --at 'YYYY-MM-DD HH:MM' --title '<标题>'
    python scripts/ledger_backfill.py wechat-add --case <cid> --appmsgid <id> \
        --at 'YYYY-MM-DD HH:MM' --title '<标题>'
    python scripts/ledger_backfill.py xhs-fix --case <cid> --note-id <id> \
        [--title '<标题>']
    python scripts/ledger_backfill.py simulate --platform bilibili

⛔ 不通用平台 API 抓「证据」：证据必须由调用方给（线上列表 + 公开页双回读），
   本工具只负责把已裁定的事实安全落账。xhs-fix 的 --online 例外只用于按
   已发表列表唯一标题取 note_id，仍要求人工核对。
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from contextlib import ExitStack
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import atomicio  # noqa: E402
import ledger as L  # noqa: E402

DEFAULT_DATA = ROOT / "data"
DEFAULT_TMP = ROOT / "tmp"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?$")


class Store:
    def __init__(self, data_dir=None, tmp_dir=None):
        self.data = Path(data_dir) if data_dir else DEFAULT_DATA
        self.db = self.data / "ledger.db"
        self.tmp = Path(tmp_dir) if tmp_dir else DEFAULT_TMP


# ---------------------------------------------------------------- 通用工具

def norm_ts(value):
    if not TS_RE.match(str(value or "")):
        raise ValueError("--at 必须是 'YYYY-MM-DD HH:MM[:SS]' 的平台真实时点，收到 %r" % value)
    return str(value).replace("T", " ")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_like(path, obj):
    """按原文件的缩进 / 换行风格原子写，避免整本格式漂移。"""
    _, _, nl, ind = L._read_file(str(path))
    text = json.dumps(obj, ensure_ascii=False, indent=ind).replace("\n", nl)
    atomicio.write_text(str(path), text, newline="")


def backup_file(path, tmp_dir, tag):
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / ("backup_%s_%s_%s" % (tag, Path(path).name,
                                          time.strftime("%Y%m%d_%H%M%S")))
    shutil.copy2(str(path), str(dst))
    return (dst, Path(path))


def rollback(backups):
    for dst, src in backups:
        shutil.copy2(str(dst), str(src))
        print("  ↩ 回滚 %s ← %s" % (src.name, dst.name))


def db_row(conn, case, platform):
    r = conn.execute("select * from platform_posts where case_id=? and platform=?",
                     (case, platform)).fetchone()
    return dict(r) if r else None


def op_note(old, tag, text):
    stamp = time.strftime("%Y-%m-%d")
    return (old or "") + "｜%s ledger_backfill/%s：%s" % (stamp, tag, text)


def _find(rows, cid):
    return [r for r in rows if str(r.get("id")) == str(cid)]


# ---------------------------------------------------------------- 纯判据（可单测）

def plan_bili(drafts, pubs, db, cid, draft_id, article_id, at, title):
    d, p = _find(drafts, cid), _find(pubs, cid)
    state = str((db or {}).get("state") or "")
    rid = str((db or {}).get("remote_id") or "")
    if p:
        if (str(p[0].get("article_id")) == str(article_id) and not d
                and state != "draft" and rid in ("", str(article_id))):
            return "skip", "已按同一 article_id 补登，无需再写"
        return "refuse", "已发侧存在 article_id=%s 或草稿行并存，需人工裁定" % p[0].get("article_id")
    if not d:
        return "refuse", "草稿侧没有该 case，缺已发证据"
    if str(d[0].get("article_id")) != str(draft_id):
        return "refuse", "草稿 id=%s 与给定 %s 不符" % (d[0].get("article_id"), draft_id)
    if state == "published" and rid not in ("", str(article_id)):
        return "refuse", "db 已 published 但 remote_id=%s 与 article_id 不同" % rid
    return "apply", "草稿 %s → 已发 %s，证据齐全" % (draft_id, article_id)


def plan_wechat(data, db, cid, appmsgid, at, title):
    if isinstance(data, dict):
        row = data.get(cid)
    else:
        row = _find(data, cid)[0] if _find(data, cid) else None
    rid = str((db or {}).get("remote_id") or "")
    if row:
        # 公众号台账的 appmsgid 是建稿时的草稿 id 镜像；群发后真 id 落在
        # published_appmsgid（PRD #83）。两个字段任一命中都算「已补过」。
        known = {str(row.get("appmsgid") or ""),
                 str(row.get("published_appmsgid") or "")}
        if str(appmsgid) in known and str(row.get("status") or "") == "published":
            return "skip", "已按同一 appmsgid 补登（草稿镜像 / 群发 id），无需再写"
        return "refuse", "已发侧 id=%s 与给定 %s 不同，需人工裁定" % (
            sorted(known), appmsgid)
    if rid not in ("", str(appmsgid)):
        return "refuse", "db remote_id=%s 与 appmsgid 不同，需人工裁定" % rid
    return "apply", "线上已发 appmsgid=%s 本地漏登，证据齐全" % appmsgid


def plan_xhs(drafts, pubs, db, cid, note_id, title):
    d, p = _find(drafts, cid), _find(pubs, cid)
    if not p:
        return "refuse", "已发侧没有该 case，补 note_id 无落点"
    cur = str(p[0].get("note_id") or "")
    if cur and cur != str(note_id):
        return "refuse", "已发侧 note_id=%s 与给定 %s 不同，需人工裁定" % (cur, note_id)
    if cur == str(note_id) and not d:
        return "skip", "note_id 已回填且草稿行已移出，无需再写"
    return "apply", "草稿行 %d 条、note_id 由 %s 补为 %s" % (len(d), cur or "(空)", note_id)


# ---------------------------------------------------------------- 写盘实现

def do_bili(args, store):
    drafts_path = store.data / "bili_drafts.json"
    pubs_path = store.data / "bili_published.json"
    with ExitStack() as stack:
        for p in (drafts_path, pubs_path, store.db):
            stack.enter_context(atomicio.file_lock(str(p) + ".lock"))
        drafts, pubs = read_json(drafts_path), read_json(pubs_path)
        conn = sqlite3.connect(str(store.db))
        conn.row_factory = sqlite3.Row
        db = db_row(conn, args.case, "bilibili")
        action, why = plan_bili(drafts, pubs, db, args.case, args.draft_id,
                                args.article_id, args.at, args.title)
        print("%s %s：%s" % (args.case, action.upper(), why))
        if action != "apply":
            conn.close()
            return 0 if action == "skip" else 2
        if not args.apply:
            conn.close()
            print("（干跑，加 --apply 才写盘）")
            return 0
        backups = [backup_file(p, store.tmp, "bili") for p in
                   (drafts_path, pubs_path, store.db)]
        try:
            new_drafts = [r for r in drafts if str(r.get("id")) != args.case]
            new_pubs = [r for r in pubs if str(r.get("id")) != args.case]
            new_pubs.append({
                "id": args.case, "at": args.at, "article_id": str(args.article_id),
                "verified_at": args.at, "title": args.title,
                "evidence": "ledger_backfill",
                "note": op_note("", "bili-promote",
                                "草稿 %s → 线上已发 %s；按平台真值时点 %s 落账"
                                % (args.draft_id, args.article_id, args.at)),
            })
            write_like(drafts_path, new_drafts)
            write_like(pubs_path, new_pubs)
            conn.execute(
                "update platform_posts set state='published', remote_id=?, draft_id='', "
                "url=?, published_at=?, updated_at=?, note=? "
                "where case_id=? and platform='bilibili'",
                (str(args.article_id), "https://www.bilibili.com/read/cv" + str(args.article_id),
                 args.at, args.at,
                 op_note((db or {}).get("note"), "bili-promote",
                         "草稿 %s → 线上已发 %s" % (args.draft_id, args.article_id)),
                 args.case))
            conn.commit()
            after = db_row(conn, args.case, "bilibili")
            assert after and after["state"] == "published" \
                and str(after["remote_id"]) == str(args.article_id) \
                and after["published_at"] == args.at, "db 回读不一致"
            assert not _find(read_json(drafts_path), args.case), "草稿行没移出"
            assert len(_find(read_json(pubs_path), args.case)) == 1, "已发行重复"
            print("OK 草稿 0 / 已发 1 / db published=%s" % after["remote_id"])
            return 0
        except BaseException as exc:
            conn.close()
            rollback(backups)
            print("✗ 失败已回滚：%s" % str(exc)[:200])
            return 2
        finally:
            try:
                conn.close()
            except Exception:
                pass


def do_wechat(args, store):
    path = store.data / "wechat_published.json"
    with ExitStack() as stack:
        stack.enter_context(atomicio.file_lock(str(path) + ".lock"))
        stack.enter_context(atomicio.file_lock(str(store.db) + ".lock"))
        data = read_json(path)
        conn = sqlite3.connect(str(store.db))
        conn.row_factory = sqlite3.Row
        db = db_row(conn, args.case, "wechat")
        action, why = plan_wechat(data, db, args.case, args.appmsgid, args.at, args.title)
        print("%s %s：%s" % (args.case, action.upper(), why))
        if action != "apply":
            conn.close()
            return 0 if action == "skip" else 2
        if not args.apply:
            conn.close()
            print("（干跑，加 --apply 才写盘）")
            return 0
        backups = [backup_file(p, store.tmp, "wx") for p in (path, store.db)]
        try:
            note = op_note("", "wechat-add",
                           "线上已发表列表唯一命中 appmsg_id=%s，按平台真值 %s 补登"
                           % (args.appmsgid, args.at))
            rec = {
                "title": args.title, "published_at": args.at,
                "status": "published", "appmsgid": str(args.appmsgid),
                "published_appmsgid": str(args.appmsgid), "file": "",
                "publish_type": "publish", "sent_via": "reconcile",
                "send_reason": "线上已发表但本地漏登，按标题唯一命中补账",
                "note": note, "verified_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            if isinstance(data, dict):
                data[args.case] = rec
            else:
                data = [r for r in data if str(r.get("id")) != args.case]
                data.append(dict(rec, id=args.case))
            write_like(path, data)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "insert or ignore into cases (id,type,status,updated_at,created_at) "
                "values (?, 'case', 'imported_stub', ?, ?)", (args.case, now, now))
            if db:
                # db 已有行而 JSON 漏登：只做单行 UPDATE，绝不 INSERT 撞唯一约束
                conn.execute(
                    "update platform_posts set state='published', remote_id=?, url=?, "
                    "title=?, draft_id='', published_at=?, updated_at=?, note=? "
                    "where case_id=? and platform='wechat'",
                    (str(args.appmsgid), args.url or (db.get("url") or ""), args.title,
                     args.at, args.at, op_note(db.get("note"), "wechat-add", note),
                     args.case))
            else:
                conn.execute(
                    "insert into platform_posts(case_id,platform,state,remote_id,url,title,"
                    "draft_id,attempt,published_at,updated_at,note) "
                    "values(?,?,?,?,?,?,?,?,?,?,?)",
                    (args.case, "wechat", "published", str(args.appmsgid),
                     args.url or "", args.title, "", 0, args.at, args.at, note))
            conn.commit()
            after = db_row(conn, args.case, "wechat")
            assert after and after["state"] == "published" \
                and str(after["remote_id"]) == str(args.appmsgid) \
                and after["published_at"] == args.at, "db 回读不一致"
            print("OK 公众号台账 1 条 / db published=%s" % after["remote_id"])
            return 0
        except BaseException as exc:
            conn.close()
            rollback(backups)
            print("✗ 失败已回滚：%s" % str(exc)[:200])
            return 2
        finally:
            try:
                conn.close()
            except Exception:
                pass


def do_xhs(args, store):
    drafts_path = store.data / "xhs_drafts.json"
    pubs_path = store.data / "xhs_published.json"
    note_id = args.note_id
    title = args.title
    if not note_id:
        if not args.online:
            print("✗ 必须给 --note-id，或加 --online 从已发表列表按标题唯一命中")
            return 2
        import xhs_webapi as X  # 延迟导入：干跑纯参数场景不需要浏览器
        cdp, _ = X.connect(9223)
        notes, _ = X.posted_notes(pages=3, cdp=cdp)
        hits = [n for n in notes if title and title in (n.get("title") or "")]
        if len(hits) != 1:
            print("✗ 线上标题命中 %d 条，需人工裁定" % len(hits))
            return 2
        note_id = str(hits[0]["note_id"])
        title = hits[0]["title"]
        print("线上唯一命中 note_id=%s title=%s" % (note_id, title))
    with ExitStack() as stack:
        for p in (drafts_path, pubs_path, store.db):
            stack.enter_context(atomicio.file_lock(str(p) + ".lock"))
        drafts, pubs = read_json(drafts_path), read_json(pubs_path)
        conn = sqlite3.connect(str(store.db))
        conn.row_factory = sqlite3.Row
        db = db_row(conn, args.case, "xiaohongshu")
        action, why = plan_xhs(drafts, pubs, db, args.case, note_id, title)
        print("%s %s：%s" % (args.case, action.upper(), why))
        if action != "apply":
            conn.close()
            return 0 if action == "skip" else 2
        if not args.apply:
            conn.close()
            print("（干跑，加 --apply 才写盘）")
            return 0
        backups = [backup_file(p, store.tmp, "xhs") for p in
                   (drafts_path, pubs_path, store.db)]
        try:
            new_drafts = [r for r in drafts if str(r.get("id")) != args.case]
            new_pubs = []
            for r in pubs:
                if str(r.get("id")) == args.case:
                    r = dict(r)
                    r["note_id"] = str(note_id)
                    if title:
                        r["title"] = title
                    r["note"] = op_note(
                        r.get("note"), "xhs-fix",
                        "按已发表列表唯一命中回填 note_id=%s，草稿行移出" % note_id)
                new_pubs.append(r)
            write_like(drafts_path, new_drafts)
            write_like(pubs_path, new_pubs)
            if db:
                conn.execute(
                    "update platform_posts set state='published', remote_id=?, title=?, "
                    "published_at=?, updated_at=?, note=? "
                    "where case_id=? and platform='xiaohongshu'",
                    (str(note_id), title or (db.get("title") or ""),
                     db.get("published_at") or args.at or "",
                     time.strftime("%Y-%m-%d %H:%M:%S"),
                     op_note(db.get("note"), "xhs-fix",
                             "回填 note_id=%s，草稿行移出" % note_id),
                     args.case))
                conn.commit()
            after = db_row(conn, args.case, "xiaohongshu")
            check = _find(read_json(pubs_path), args.case)
            assert len(check) == 1 and str(check[0].get("note_id")) == str(note_id), \
                "JSON 回读不一致"
            assert not _find(read_json(drafts_path), args.case), "草稿行没移出"
            print("OK 已发 note_id=%s / db=%s" % (note_id, after["remote_id"] if after else "无行"))
            return 0
        except BaseException as exc:
            conn.close()
            rollback(backups)
            print("✗ 失败已回滚：%s" % str(exc)[:200])
            return 2
        finally:
            try:
                conn.close()
            except Exception:
                pass


def do_simulate(args, store):
    """只读模拟 L.save() 会改什么 —— 不能当补账工具用。"""
    import difflib
    recs = L.load(args.platform)
    real_write, real_sync = L._write_file, L._db_sync
    captured = {}
    try:
        L._write_file = lambda p, v, nl, ind: captured.setdefault(
            "file:" + str(p), (v, nl, ind))
        L._db_sync = lambda plat, rec: captured.setdefault("db:" + rec["id"], rec)
        L.save(args.platform, recs)
    finally:
        L._write_file, L._db_sync = real_write, real_sync
    for key, val in captured.items():
        if key.startswith("file:"):
            p = key[5:]
            before = open(p, encoding="utf-8").read().splitlines()
            v, nl, ind = val
            after = json.dumps(v, ensure_ascii=False, indent=ind).replace("\n", nl).splitlines()
            diff = list(difflib.unified_diff(before, after, lineterm="", n=1))
            print("文件 %s：%d 行差异" % (p, len(diff)))
            print("\n".join(diff[:40]))
    changes = 0
    conn = sqlite3.connect(str(store.db))
    conn.row_factory = sqlite3.Row
    for key, rec in captured.items():
        if not key.startswith("db:"):
            continue
        row = db_row(conn, rec["id"], args.platform)
        fields = {
            "state": rec.get("state") or "draft",
            "remote_id": rec.get("remote_id") or "",
            "draft_id": rec.get("draft_remote_id") or "",
            "published_at": None if (rec.get("state") or "draft") in ("draft", "deleted")
            else (rec.get("at") or ""),
            "updated_at": rec.get("at") or "",
            "note": rec.get("note") or "",
        }
        for k, v in fields.items():
            if (row or {}).get(k) != v:
                changes += 1
                if changes <= 30:
                    print("db %s.%s: %r -> %r" % (rec["id"], k, (row or {}).get(k), v))
    conn.close()
    print("db 字段差异共 %d 处（只读模拟，未写盘）" % changes)
    return 0


# ---------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--apply", action="store_true", help="真写盘；缺省干跑")
        p.add_argument("--data-dir", default=str(DEFAULT_DATA), help=argparse.SUPPRESS)
        p.add_argument("--tmp-dir", default=str(DEFAULT_TMP), help=argparse.SUPPRESS)

    b = sub.add_parser("bili-promote", help="B站 草稿→已发 PROMOTE")
    b.add_argument("--case", required=True)
    b.add_argument("--draft-id", required=True)
    b.add_argument("--article-id", required=True)
    b.add_argument("--at", required=True, help="公开页真实发表时点")
    b.add_argument("--title", required=True)
    common(b)

    w = sub.add_parser("wechat-add", help="公众号已发侧漏登补账")
    w.add_argument("--case", required=True)
    w.add_argument("--appmsgid", required=True)
    w.add_argument("--at", required=True, help="公开页真实发表时点")
    w.add_argument("--title", required=True)
    w.add_argument("--url", default="", help="公开页 URL（可选，避免编造链接）")
    common(w)

    x = sub.add_parser("xhs-fix", help="小红书 草稿行移出 + note_id 回填")
    x.add_argument("--case", required=True)
    x.add_argument("--note-id", default="")
    x.add_argument("--title", default="")
    x.add_argument("--online", action="store_true", help="按 --title 从 9223 已发表列表取 note_id")
    x.add_argument("--at", default="", help="仅 db 无行需要补 published_at 时用")
    common(x)

    s = sub.add_parser("simulate", help="只读模拟 L.save() 的字段洗")
    s.add_argument("--platform", required=True)
    s.add_argument("--data-dir", default=str(DEFAULT_DATA), help=argparse.SUPPRESS)
    s.add_argument("--tmp-dir", default=str(DEFAULT_TMP), help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    store = Store(a.data_dir, a.tmp_dir)
    try:
        if getattr(a, "at", ""):
            a.at = norm_ts(a.at)
    except ValueError as exc:
        print("✗ %s" % exc)
        return 2
    if a.cmd == "bili-promote":
        return do_bili(a, store)
    if a.cmd == "wechat-add":
        return do_wechat(a, store)
    if a.cmd == "xhs-fix":
        return do_xhs(a, store)
    if a.cmd == "simulate":
        return do_simulate(a, store)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
