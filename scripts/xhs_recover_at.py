# -*- coding: utf-8 -*-
"""从 git 历史快照捞回 `data/xhs_drafts.json` 被洗掉的时间戳。

背景（2026-10-15）：`xhs_publish._mark()` 旧实现只取 id 列表、丢掉每条
原有的 `at`，追加一条就把**整本台账**的 `at` 全部重写成当前时间
⇒ 10-15 11:11 那次补发后，58 条全部变成 `2026-10-15 11:11`，真实
发布时间不可读。

捞回原理：对每个历史提交解出该时刻的台账快照，记下「某 id 首次出现
在哪个提交」→ 那个提交的 committer time 就是它的落账时间上界（取最早
快照即可，比后续任何 _mark 洗出来的值都可信）。

⚠ 只写 `data/xhs_drafts.json`，且默认 dry-run，必须显式 `--apply`。
⚠ 绝不碰 xhs_published.json（2 条还准，且是另一个文件）。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "data", "xhs_drafts.json")


def _git(*args):
    return subprocess.run(["git", "-C", ROOT] + list(args),
                          capture_output=True, check=False)


def _snapshots():
    """按时间正序返回 [(committer_time, {id: at})]。"""
    r = _git("log", "--reverse", "--format=%H|%cI", "--", "data/xhs_drafts.json")
    if r.returncode != 0:
        raise SystemExit("git log 失败：%s" % r.stderr.decode("utf-8", "replace"))
    out = []
    for line in r.stdout.decode("utf-8").splitlines():
        if "|" not in line:
            continue
        sha, iso = line.split("|", 1)
        blob = _git("show", "%s:data/xhs_drafts.json" % sha)
        if blob.returncode != 0 or not blob.stdout.strip():
            continue
        try:
            d = json.loads(blob.stdout.decode("utf-8"))
        except Exception:
            continue
        ids = set(d.keys()) if isinstance(d, dict) else {
            e.get("id") for e in d if isinstance(e, dict) and e.get("id")}
        out.append((iso[:16].replace("T", " "), ids))
    return out


def recover():
    snaps = _snapshots()
    cur = json.load(open(LEDGER, encoding="utf-8"))
    first = {}
    for ts, ids in snaps:                     # 时间正序 ⇒ 首次出现即最早
        for i in ids:
            first.setdefault(i, ts)
    fixed, unknown = [], []
    for e in cur:
        cid = e.get("id")
        hit = first.get(cid)
        if hit and hit != e.get("at"):
            fixed.append((cid, e.get("at"), hit))
        elif not hit:
            unknown.append(cid)
    return cur, fixed, unknown, len(snaps)


def main():
    apply = "--apply" in sys.argv
    cur, fixed, unknown, n = recover()
    print("历史快照 %d 个，当前台账 %d 条" % (n, len(cur)))
    print("可捞回 %d 条，历史里查不到 %d 条%s"
          % (len(fixed), len(unknown), ("：%s" % unknown) if unknown else ""))
    if not apply:
        print("\n-- 未加 --apply，只预览不写盘。前 10 条：")
        for cid, was, now in fixed[:10]:
            print("  %-42s %s -> %s" % (cid, was, now))
        return
    if not fixed:
        print("没有要改的条目，不写盘。")
        return
    # 备份再改
    bak = os.path.join(tempfile.gettempdir(), "xhs_drafts_recover.bak")
    with open(LEDGER, "rb") as f, open(bak, "wb") as g:
        g.write(f.read())
    print("\n已备份到 %s" % bak)
    table = {cid: now for cid, _w, now in fixed}
    raw = open(LEDGER, "rb").read()
    crlf = raw.count(b"\r\n")
    nl = "\r\n" if crlf > raw.count(b"\n") - crlf else "\n"
    for e in cur:
        if e.get("id") in table:
            e["at"] = table[e["id"]]
    text = json.dumps(cur, ensure_ascii=False, indent=2).replace("\n", nl)
    with open(LEDGER, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print("已写回 %d 条时间戳" % len(fixed))


if __name__ == "__main__":
    main()