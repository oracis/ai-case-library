"""Regenerate local title artifacts after the 2026-10-14 title red-line fix.

Two pieces of local data carry the title that actually gets published:
  1. out/toutiao/<id>/meta.json  -- read by toutiao_publish at publish time
  2. data/publish_manifest.json  -- the four-platform ledger (wechat/toutiao/xhs
     title fields were never populated by publish; this makes them honest records)

Both are regenerated from the now-fixed title generators so local artifacts match
what re-save will push. Pure Python, no Chrome. Preserves file line endings.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import toutiao_publish as tp          # noqa: E402
import wechat_publish as wp           # noqa: E402
import xhs_publish as x              # noqa: E402

ROOT = tp.ROOT
CASES = tp.load_cases()
BY_ID = {c["id"]: c for c in CASES}


def main():
    # ---- 1) toutiao meta.json (build_article -> write_article) ----
    n_meta = 0
    for c in CASES:
        meta = tp.build_article(c)
        tp.write_article(meta)        # writes out/toutiao/<id>/{meta.json,article.html}
        n_meta += 1
    print("[1] regenerated toutiao meta.json for %d cases" % n_meta)

    # ---- 2) manifest wechat/toutiao/xhs title fields ----
    mp = os.path.join(ROOT, "data", "publish_manifest.json")
    raw = open(mp, "rb").read()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    man = json.loads(raw.decode("utf-8"))

    fns = {
        "wechat": lambda c: wp.make_wechat_title(c),
        "toutiao": lambda c: tp.make_toutiao_title(c),
        "xhs": lambda c: x.make_xhs_title(c),
    }
    n_man = 0
    for cid, entry in man.items():
        c = BY_ID.get(cid)
        if not c:
            continue
        for plat, fn in fns.items():
            if plat in entry and isinstance(entry[plat], dict):
                entry[plat]["title"] = fn(c)
                n_man += 1
    # ⚠ 缩进必须是 1 + sort_keys：publish_manifest.json 的规范格式在
    #   `multiplatform/state.py` 的 `Manifest.save`（indent=1, sort_keys=True）。
    #   2026-10-20 之前这里是 indent=2，每次跑完 git diff 多出三千行
    #   纯格式噪音（与 `bilibili_publish._mark_manifest_published` 同病）。
    text = json.dumps(man, ensure_ascii=False, indent=1, sort_keys=True)
    text = text.replace("\n", eol)    # 保留原行尾
    with open(mp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        if not text.endswith(eol):
            f.write(eol)
    print("[2] updated manifest title fields for %d platform-entries" % n_man)


if __name__ == "__main__":
    main()
