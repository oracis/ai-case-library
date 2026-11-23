#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性修复：PRD #214 被 shell 反引号/美元符号展开后的坏行。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

PATH = os.path.join(ROOT, "docs", "PRD.md")

ROW = """| 214 | **全平台发布一轮（11-23）：八入口真发、头条冻结/B站二号轮空；三篇无稿稿加厚与合规修正，审核态回读后升账** | 🗣 用户「全平台发布」，确认「按计划表推进 + 公众号走 FIFO 最久 Wagiff App + 全程在场扫码/滑块 + 数字按库内真值/下行 -15.2%/加厚 2000+/CSDN 放宽同日同类/腾讯云手写技术稿」。S0 只读：`preflight.py` + `publish_slot.py` + `publish_one --all` + `ledger_health --verbose` + `yun_login_keep --check`，发前 `ledger_health --online`（B站/头条/小红书）+ `pub_reconcile.py`（五家）；历史待处理项（头条 3 STALE、小红书 checkvibe/2048、CSDN publbee NO_ID、博客园 clawlink DUP_PUB、腾讯云 0 A.D. 标题漂移）只记录未动。<br>**逐家结果（独立回读，不采脚本自报）**：公众号 FIFO `wagiff-app` 草稿 `100000502` → 新 `2247485065`，已发表列表独立 GET `fetch_ok=True total=63` / `HIT_COUNT 1` / `NEW_ID_OK True`；知乎积压 `sleek-design` `2099504242616752000` → published，`pub_reconcile --platform zhihu` 线上 15→16、本地 16、零待处理；小红书草稿按标题「多语言真人语音接打电话」发布 → note_id `6b03e02a000000002500a70c` / 14:41，脚本报「没对上案例 id 未记账」⇒ 按接口回读补 `xhs_published.json` + db 单行，并清 `xhs_drafts.json` 残留行；B站 `ext-ep-004-quake` 先 `bilibili_publish.py build`（1213 字），封面 7,125,195 B 超 5MB ⇒ `bili_cover_shrink.py` 压到 409,697 B（1920x1077），`cover --api --force` 补到 aid 347713，`send-draft --case` 却按标题取更早旧稿 345600 发出 → `54045110`，残留 347713 经用户授权走 `bili_draft_api.delete_draft` 返回 `{'code':0,'message':'OK'}`，草稿箱回读消失；博客园 `podawaa` → `23754964`，MetaWeblog list 命中 + 匿名 GET 5 关键词全中；CSDN `podawaa` → `171238734`，首次 list 未命中且公开页显示「审核中」⇒ 先落 `auditing / published_at=NULL`，复跑 list 命中 `postTime=2026-11-23 14:54:41` ⇒ 单行 UPDATE 升 `published`；腾讯云手写技术稿 `blazedocs-io` 2834 字 → `2791357`，匿名 GET 200 / 55412 bytes / 标题命中 / 无审核横幅 / 封面落库；阿里云 `magicslides-app` → `1787703`，滑块人工拖过后匿名 GET 404 ⇒ `auditing / published_at=NULL`，`yun_state.py` 复核仍 404/auditing，不重发。<br>**生成稿修正**（用户裁定后）：博客园/CSDN Podawaa 从 1174/1184 字加厚到 2130/2106 字，阿里云 MagicSlides 从 666 字加厚到 2192 字；删 `$66万/月`「66 美元万/月」错写，统一 MRR $65,990 / 累计 $1,200,583 / 613 订阅（2026-10-10）与 MRR 11,332 / 累计 714,537 美元（2026-09-20），下行只写 -15.2%；`metric_note` / `corrections` / `候选` 三个内部字段名清掉；CSDN `katex_money_fix.py` 改 8 处 `$N`，`csdn_rules.py` 金额密度 10→2 / 商业化叙述 23→0，站外联系方式 2→0；腾讯云补封面 `out/articles/blazedocs-io-cover.png`（600x360）。<br>**收尾**：`ledger_health --verbose` 剩 3 项历史小红书告警（checkvibe DUP/NO_ID、ext-ep-002-2048 NO_ID），`pub_reconcile.py` 剩 3 项历史项（CSDN publbee NO_ID、博客园 clawlink DUP_PUB、腾讯云 0 A.D. TITLE_DRIFT），均非本轮；`publish_slot.py` 复核公众号/小红书/B站/知乎/阿里云各 1 篇达上限，头条仍冻结。备份 `tmp/ledger_backup_20261123_*.db`、`tmp/xhs_*_backup_*.json`；未提交、未推远端。 |"""


def main():
    text = atomicio.read_text(PATH) if hasattr(atomicio, "read_text") else open(PATH, encoding="utf-8").read()
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.startswith("| 214 |")]
    if len(idx) != 1:
        raise SystemExit("expected exactly one PRD 214 row, got %d" % len(idx))
    lines[idx[0]] = ROW
    atomicio.write_text(PATH, "\n".join(lines) + "\n") if hasattr(atomicio, "write_text") else open(PATH, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("FIXED", PATH, "line", idx[0] + 1, "len", len(ROW))


if __name__ == "__main__":
    main()
