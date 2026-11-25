"""把 ai-prank-call 的 manifest 小红书条目改成 deleted（2026-11-25 用户裁定）。

背景：ai-prank-call 在小红书的两条笔记都被删了 ——
  · 6b06296f000000002b01bfe3（11-25 08:18 误发）先删；
  · 6adefddb0000000015004eb5（10-26 15:14 原稿）用户裁定「删存入久的、不补发」后删。
线上现在零笔记，db 行与 `xhs_published.json` 都已由 `xhs_delete.py` 清掉，
只剩 `publish_manifest.json` 还挂着 published + remote_id，属陈旧镜像。

⛔ 不调 `Manifest.save()`（整本重刷会带出全表回退）。只改这一个子键里的三个字段，
其余原样写回，格式 indent=1 / 无尾换行，写完回读。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
CASE = "ai-prank-call"
NOTE = "[11-25 清重复稿] 用户裁定「删存入久的、不补发」：两条小红书笔记均已删除并回读线上为零，state 置 deleted，明天档期可重发（dedupe 不拦 deleted）"


def main():
    apply = "--yes" in sys.argv
    with open(MANIFEST, encoding="utf-8") as f:
        before = json.load(f)
    after = json.loads(json.dumps(before))

    node = after.get(CASE) or {}
    prev = node.get("xhs") or {}
    if not isinstance(prev, dict):
        raise SystemExit("✗ manifest 里 %s.xhs 不是对象，停手" % CASE)
    entry = dict(prev)
    entry["state"] = "deleted"
    entry["published_at"] = None
    entry["note"] = NOTE

    node["xhs"] = entry
    after[CASE] = node

    changed = []
    for cid in set(before) | set(after):
        b, a = before.get(cid) or {}, after.get(cid) or {}
        for plat in set(b) | set(a):
            if b.get(plat) != a.get(plat):
                changed.append("%s.%s" % (cid, plat))
    lost = [k for k in before if k not in after]
    print("变更项:", changed, "| 丢键:", lost or "无")
    if lost or changed != ["%s.xhs" % CASE]:
        raise SystemExit("✗ 变更超出预期，停手")

    if not apply:
        print("（干跑）确认无误后加 --yes")
        return 0

    text = json.dumps(after, ensure_ascii=False, indent=1)
    with atomicio.file_lock(MANIFEST + ".lock"):
        atomicio.write_text(MANIFEST, text)

    with open(MANIFEST, encoding="utf-8") as f:
        back = json.load(f)
    got = (back.get(CASE) or {}).get("xhs") or {}
    print("回读:", json.dumps(got, ensure_ascii=False))
    print("条目总数 before/after:", len(before), len(back))
    ok = got.get("state") == "deleted" and len(before) == len(back)
    print("✓ manifest 已改 deleted" if ok else "✗ 回读不符")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
