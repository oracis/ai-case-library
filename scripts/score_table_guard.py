#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评分静态表的「只存数值」守卫 + 历史写入脚本的封存口（PRD #186）。

背景（PRD #184 → #186）
----------------------
#184 把 ``SCORES`` / ``DELIVERY`` 两张人工表收敛成**只存数值**：

  * ``SCORES`` 每条只剩六维 + blocker，不再有 ``note=``；
  * ``DELIVERY`` 每条第二位恒为 ``NOTE_IN_LIBRARY``（空串），保留 tuple 形状。

文案的唯一真值是 ``case_library`` 里的 ``china_fit.note`` /
``solo_fit.delivery_note``；``test_fit_ready.test_tables_have_no_note_copies``
守住「静态表不许再有第二份文案」。

但 ``scripts/_oneoff/apply_fit_scores_20261106.py`` 与 ``merge_c_tables.py``
是 #45 / #64 遗留的一次性脚本，生成的表条目**自带** ``note=`` 与非空理由。
它们当前只靠「4 条 id 已存在」/「C 档 banner 注释还在」两道幂等闸挡住重跑；
锚点一旦在整理表、清理注释、或把脚本当模板补新条目时失效，就会把文案副本
重新写回静态表 ⇒ 上面那条测试当场报红。**那不是测试的误报**：是脚本真的会
污染，所以这里从执行层封死，而不是放宽或忽略测试。

本模块提供两样东西
------------------
  * ``note_copy_ids(scores, delivery)`` / ``table_is_note_free(...)``：
    「两表无文案副本」的**数据级判据单一源**，纯函数只读，供测试与护栏复用。
  * ``refuse_legacy_writer(script_name)``：历史脚本的封存口，无条件
    ``SystemExit``，无开关、不读表、不写盘。两个历史脚本在 ``main()`` 第一行
    调用它；原生成逻辑原样保留，只作历史复现留档。

⛔ 不要给封存口加 ``--force`` / ``--yes`` 之类的开关：#184 刚删掉
   ``--refresh-notes``，``test_no_refresh_backdoor`` 钉着「不许有覆盖后门」。
"""
from __future__ import annotations

#: 会往静态表写「文案副本」的历史一次性脚本（相对 scripts/_oneoff/）。
#: 新增同类脚本时必须一并接封存口并加进这里 —— 测试会遍历本清单。
LEGACY_WRITERS = ("apply_fit_scores_20261106.py", "merge_c_tables.py")

_SEALED = """[SEALED] %(script)s 已封存，拒绝运行（PRD #186）。

  这是 PRD #45 / #64 遗留的一次性脚本：它生成的表条目自带 note= 与理由文案。
  PRD #184 之后 SCORES / DELIVERY 只存数值，文案唯一真值在 case_library
  （china_fit.note / solo_fit.delivery_note）；重跑或拿它当模板补条目，都会把
  文案副本写回静态表，test_tables_have_no_note_copies 当场报红 —— 那是真污染，
  不是误报。原生成逻辑保留在此仅供历史复现，不可以再执行。

  正确做法：
    * 改分数 / 六维 / blocker → 直接改 SCORES / DELIVERY 的数值，再重跑
      score_china_fit.py / score_solo_fit.py 让派生分跟上；
    * 改文案 → 回 case_library 改（跑分走 score_note_policy.resolve_note）；
    * 对账   → score_value_parity.py --check / fit_ready.py --check；
    * 清副本 → scripts/_oneoff/strip_score_table_notes.py --check。
  历史依据见 docs/PRD.md #45 / #64 / #184 / #186。
"""


def note_copy_ids(scores, delivery):
    """只读：返回 ``(SCORES 里带 note 的 id, DELIVERY 里理由非空的 id)``。

    判据与 ``test_tables_have_no_note_copies`` 一致，只是把命中的 id 报出来，
    方便定位是「谁」把副本写回了表，而不是只给一个计数。
    """
    bad_scores = [cid for cid, row in scores.items() if "note" in row]
    bad_delivery = [cid for cid, row in delivery.items()
                    if len(row) > 1 and bool(row[1])]
    return bad_scores, bad_delivery


def table_is_note_free(scores, delivery):
    """两表是否都没有文案副本（纯函数，只读）。"""
    bad_scores, bad_delivery = note_copy_ids(scores, delivery)
    return not bad_scores and not bad_delivery


def refuse_legacy_writer(script_name):
    """封存口：无条件拒绝历史脚本运行。

    无参数开关、不读表、不写盘 —— 调用即退出，保证重跑在任何文件读写之前终止。
    """
    raise SystemExit(_SEALED % {"script": script_name})
