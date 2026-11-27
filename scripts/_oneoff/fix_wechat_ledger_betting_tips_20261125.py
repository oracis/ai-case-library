"""修 `data/wechat_published.json` 里 betting-tips 的台账漂移（单键手术）。

背景（2026-11-25）
------------------
08:15 群发 `betting-tips` 后，补账只写了 `publish_manifest.json` 与 db
（`platform_posts.remote_id=2247485067` / `published_at=2026-11-25 08:15`），
JSON 台账没跟着改 ⇒ 同一篇出现两个真相：
    JSON : status=draft, published_at="", appmsgid=100001002（旧草稿 id）
    db/manifest : status=published, published_at=2026-11-25 08:15,
                  published_appmsgid=2247485067
这会让 `load_published()` 的并集视图在「草稿/已发」之间摇摆，也是
`_rows_wechat` 判重与今日计数的隐患。

本脚本只改这一条，逐字段写**与 db/manifest 一致**的值，并保留 JSON 独有的
审计列（publish_type / sent_via / send_reason / note / verified_at）。
⛔ 不整本重刷、不动其它 81 条 —— 写完逐条对比证明只变了这一个键。

用法：
    python scripts/_oneoff/fix_wechat_ledger_betting_tips_20261125.py        # 干跑
    python scripts/_oneoff/fix_wechat_ledger_betting_tips_20261125.py --yes  # 真写
"""
import argparse
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio  # noqa: E402

PATH = os.path.join(ROOT, "data", "wechat_published.json")
CASE = "betting-tips"

# 目标值：全部取自 db / manifest（唯一真相），不在这里发明新数字。
TARGET = {
    "status": "published",
    "published_at": "2026-11-25 08:15",
    "published_appmsgid": "2247485067",
    "publish_type": "masssend",          # 「群发」（开关开）＝ masssend
    "sent_via": "send",                  # 自动发表（区别于后台手点 backend_manual）
    "send_reason": "已发表列表出现新记录，标题一致",
    "verified_at": "2026-11-25 09:10",   # 补账时点
    "note": "｜11-25 补账：群发后 appmsgid=2247485067；同日修正 JSON 侧仍为 draft 的漂移",
}


def main():
    ap = argparse.ArgumentParser(description="修公众号 JSON 台账 betting-tips 漂移")
    ap.add_argument("--yes", action="store_true", help="真写（缺省干跑）")
    a = ap.parse_args()

    text = atomicio.read_text(PATH)
    crlf = "\r\n" in text
    obj = json.loads(text)
    if CASE not in obj:
        raise SystemExit("✗ JSON 里没有 %s 这条" % CASE)
    before = copy.deepcopy(obj)
    rec = obj[CASE]
    print("修复前：%s" % json.dumps(rec, ensure_ascii=False))
    print("目标值：%s" % json.dumps(TARGET, ensure_ascii=False))

    changed = {k: v for k, v in TARGET.items() if rec.get(k) != v}
    if not changed:
        print("✓ 已是目标状态，无需改动")
        return 0
    rec.update(changed)
    print("将改字段：%s" % ", ".join(sorted(changed)))

    if not a.yes:
        print("\n（干跑）加 --yes 真写")
        return 0

    new_text = json.dumps(obj, ensure_ascii=False, indent=2)
    if crlf:
        new_text = new_text.replace("\n", "\r\n")
    with atomicio.file_lock(PATH + ".lock"):
        atomicio.write_text(PATH, new_text)

    # ---- 回读校验 ----
    back_text = atomicio.read_text(PATH)
    back = json.loads(back_text)
    assert len(back) == len(before), "条目数变了：%d → %d" % (len(before), len(back))
    diffs = [k for k in before if back.get(k) != before[k]]
    print("\n回读：条目 %d 条（与改前一致）｜变动的键：%s" % (len(back), diffs))
    if diffs != [CASE]:
        raise SystemExit("✗ 只应变动 %s，实际 %s" % (CASE, diffs))
    print("修复后：%s" % json.dumps(back[CASE], ensure_ascii=False))
    print("✓ 单键修正完成（CRLF=%s，尾换行=%s）"
          % (crlf, back_text.endswith("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
