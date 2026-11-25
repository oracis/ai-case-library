"""给 publish_manifest.json 补一条 cnblogs 记录（单键合并，不整本重刷）。

背景：`cnblogs_publish.py publish` 只写 `cnblogs_drafts.json` + db（真值），
manifest 需要单独补镜像。⛔ 不许调 `Manifest.save()`（它会按 db 覆盖 state/at，
整本落盘带出全表回退，PRD #203）。这里只做「读 → 加一个子键 → 原样写回」，
格式保持 indent=1 + 无尾换行，写完立刻回读校验。

用法：
    python scripts/_oneoff/record_cnblogs_manifest_20261125.py \
        --case ai-prank-call --post-id 23775202 \
        --url https://www.cnblogs.com/thing-explainer/p/23775202 \
        --title "实时语音恶作剧电话：延迟预算、成本结构与订阅天花板" \
        --published-at "2026-11-25 09:45:34"
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--post-id", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--published-at", required=True)
    ap.add_argument("--note", default="")
    ap.add_argument("--yes", action="store_true", help="真写；缺省只打印 diff")
    args = ap.parse_args()

    with open(MANIFEST, encoding="utf-8") as f:
        before = json.load(f)

    after = json.loads(json.dumps(before))            # 深拷贝，别动 before
    node = after.setdefault(args.case, {})
    if not isinstance(node, dict):
        raise SystemExit("✗ manifest 里 %s 不是对象，停手人工看" % args.case)
    prev = node.get("cnblogs") or {}
    entry = dict(prev) if isinstance(prev, dict) else {}
    entry.update({
        "state": "published",
        "remote_id": args.post_id,
        "title": args.title,
        "url": args.url,
        "at": args.published_at,
        "draft_id": args.post_id,
        "published_at": args.published_at,
        "attempt": entry.get("attempt", 0),
    })
    if args.note:
        entry["note"] = args.note
    node["cnblogs"] = entry

    # 语义比对：只允许「本 case 的 cnblogs」发生变化
    changed = []
    for cid in set(before) | set(after):
        b = before.get(cid) or {}
        a = after.get(cid) or {}
        for plat in set(b) | set(a):
            if b.get(plat) != a.get(plat):
                changed.append("%s.%s" % (cid, plat))
    lost = [k for k in before if k not in after]
    print("变更项:", changed)
    print("丢键:", lost)
    if lost or any(not c.startswith(args.case + ".") for c in changed):
        raise SystemExit("✗ 变更超出预期范围，停手")

    if not args.yes:
        print("（干跑）确认无误后加 --yes")
        return

    with atomicio.file_lock(MANIFEST + ".lock"):
        atomicio.write_text(
            MANIFEST,
            json.dumps(after, ensure_ascii=False, indent=1),
        )
    # 回读
    with open(MANIFEST, encoding="utf-8") as f:
        back = json.load(f)
    got = (back.get(args.case) or {}).get("cnblogs") or {}
    ok = (got.get("remote_id") == args.post_id
          and got.get("title") == args.title
          and got.get("state") == "published")
    print("回读:", json.dumps(got, ensure_ascii=False))
    print("条目总数 before/after:", len(before), len(back))
    print("✓ manifest 已补登" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
