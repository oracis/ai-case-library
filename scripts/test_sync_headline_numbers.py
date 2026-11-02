# -*- coding: utf-8 -*-
"""sync_headline_numbers.py 的测试：口径标签定位必须精准。

为什么单独测这个：脚本把**写死在文案里的数字**换成实拉现值。
替换错一次，比不替换更坏 —— headline 会变成自相矛盾的话
（「近 30 天收入 $1,666」配着 last30=1,436），下游直接抄走。

2026-11-01 实测踩到两个坑，都钉在这里：
  ① **同值陷阱**：bobby 的 last30 与 all_time 旧值**恰好相同**（都是 1,436）。
     只按「旧值相等」定位 ⇒ 按 all_time 替换 ⇒ headline 里的「近 30 天收入」
     被写成累计口径（$1,666）。修法：先看文案的口径标签。
  ② **语境误伤**：「近 30 天 MRR 增速 +12%」「历史峰值 $4,251」
     「付费订阅 1077 户」—— 增速/峰值不是当前金额，数字也不能被当别人的前缀。

用法：python scripts/test_sync_headline_numbers.py
"""

import importlib.util
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

spec = importlib.util.spec_from_file_location(
    "_shn", os.path.join(REPO, "scripts", "sync_headline_numbers.py"))
SH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SH)

fails = []
passed = 0


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def sync(text, old_map, new_map):
    return SH.sync_text(text, old_map, new_map)


# --------------------------------------------------------------- ① 同值陷阱

def t_same_value_trap():
    """bobby 实况：last30 旧值 == all_time 旧值 == 1436。

    headline 说「近 30 天收入」⇒ 必须换成 last30 的新值 1436，
    绝不能换成 all_time 的新值 1666。
    """
    old = {"mrr": 0.0, "last_30d_revenue": 1436.0, "all_time": 1436.0,
           "subscriptions": 0}
    new = {"mrr": 0.0, "last_30d_revenue": 1436.0, "all_time": 1666.0,
           "subscriptions": 0}
    text = "近 30 天收入 $1,436，TrustMRR 实时核对（非订阅制，MRR 恒 0）"
    out, hits = sync(text, old, new)
    chk("同值陷阱：headline 保持近30天口径",
        "$1,436" in out and "$1,666" not in out, repr(out))

    # metric_note 里两个数都在，各自要对上自己的字段
    text2 = ("实测口径：近 30 天已验证收入 $1,436（非订阅制/买断制业务，"
             "MRR 恒 0；TrustMRR 从支付网关 API 直读，2026-10-28 同步，"
             "Stripe (API key)）。近 30 天收入 $1,436、累计 $1,436、"
             "付费订阅 0 户。")
    out2, _ = sync(text2, old, new)
    chk("同值陷阱：metric_note 近30天仍是 1,436",
        "近 30 天收入 $1,436" in out2, repr(out2[60:130]))
    chk("同值陷阱：metric_note 累计变成 1,666",
        "累计 $1,666" in out2, repr(out2[60:130]))


# --------------------------------------------------------------- ② 语境误伤

def t_context_guard():
    """增速 / 历史峰值 / 订阅数 不是当前金额，不能被替换。"""
    old = {"mrr": 2039.0, "last_30d_revenue": 2193.0, "all_time": 9508.0,
           "subscriptions": 52}
    new = {"mrr": 4251.0, "last_30d_revenue": 2950.0, "all_time": 10478.0,
           "subscriptions": 107}
    text = ("实测口径：MRR $2,039（TrustMRR 从支付网关 API 直读，2026-10-28 "
            "同步，RevenueCat (API key)）。近 30 天收入 $2,193、累计 $9,508、"
            "付费订阅 52 户。近 30 天 MRR 增速 +20.6%。"
            "⚠ 历史峰值 $3,100 → 最近完整月 $1,991，留存 97%。")
    out, _ = sync(text, old, new)
    chk("MRR 换成新值", "MRR $4,251" in out, repr(out[:60]))
    chk("近30天收入换成新值", "近 30 天收入 $2,950" in out)
    chk("累计换成新值", "累计 $10,478" in out)
    chk("付费订阅换成新值且没多出数字",
        "付费订阅 107 户" in out, repr(out[70:120]))
    chk("增速百分比不被当成 MRR 金额",
        "MRR 增速 +20.6%" in out, repr(out[-160:-80]))
    chk("历史峰值不被当成当前 MRR",
        "历史峰值 $3,100" in out, repr(out[-160:-80]))


def t_stale_wrong_number():
    """文案里的数是**上一轮错改的残留**，而该字段这次新旧同值 ⇒ 仍要改回来。

    bobby 实况：last30 新旧都是 1,436，但文案里写着 $1,666（上轮按
    all_time 错改的）。若用「字段旧值==新值 ⇒ 跳过」判据，就永远修不回来。
    """
    old = {"mrr": 0.0, "last_30d_revenue": 1436.0, "all_time": 1666.0,
           "subscriptions": 0}
    new = {"mrr": 0.0, "last_30d_revenue": 1436.0, "all_time": 1666.0,
           "subscriptions": 0}
    text = "近 30 天收入 $1,666，TrustMRR 实时核对（非订阅制，MRR 恒 0）"
    out, hits = sync(text, old, new)
    chk("残留错值被改回（字段同值也要改）",
        "$1,436" in out and "$1,666" not in out, repr(out))
    chk("改的动作有记账", len(hits) >= 1, repr(hits))


def t_history_snapshot_untouched():
    """「⚠ 队列缓存值是 2026-10-22 采集时的快照（当时 $1,400）」

    那个 $1,400 指的是**10-22 采集那一刻**的值，跟今天实测无关 ——
    同步成今天的新值，句子就变成「当时 $1,351」，语义直接坏掉。
    """
    old = {"mrr": 1400.0, "last_30d_revenue": 1127.0, "all_time": 14372.0,
           "subscriptions": 213}
    new = {"mrr": 1351.0, "last_30d_revenue": 1003.0, "all_time": 14455.0,
           "subscriptions": 211}
    text = ("实测口径：MRR $1,400（TrustMRR 从支付网关 API 直读，2026-10-28 "
            "同步，Stripe (API key)）。近 30 天收入 $1,127、累计 $14,372、"
            "付费订阅 213 户。⚠ 历史峰值 $2,057 → 最近完整月 $1,209。"
            "⚠ 队列缓存值是 2026-10-22 采集时的快照（当时 $1,400），"
            "与上述实测不同；本条以实测为准。")
    out, _ = sync(text, old, new)
    chk("当前 MRR 被同步", "MRR $1,351" in out, repr(out[:40]))
    chk("「当时 $1,400」不被同步（历史语境）",
        "当时 $1,400" in out, repr(out[-90:]))
    chk("「历史峰值 $2,057」不被同步",
        "历史峰值 $2,057" in out, repr(out[-140:-60]))


# --------------------------------------------------------------- ③ 普通情形

def t_normal():
    """最常见形态：MRR 型 headline + 标准 metric_note。"""
    old = {"mrr": 1400.0, "last_30d_revenue": 1127.0, "all_time": 14372.0,
           "subscriptions": 213}
    new = {"mrr": 1351.0, "last_30d_revenue": 1003.0, "all_time": 14455.0,
           "subscriptions": 211}
    hl = "MRR $1,400，TrustMRR 实时核对"
    out, hits = sync(hl, old, new)
    chk("MRR 型 headline 同步", out == "MRR $1,351，TrustMRR 实时核对", repr(out))
    chk("命中记录非空", len(hits) == 1, repr(hits))


def t_no_change():
    """旧值 == 新值时不该有任何改动。"""
    same = {"mrr": 100.0, "last_30d_revenue": 90.0, "all_time": 900.0,
            "subscriptions": 10}
    out, hits = sync("MRR $100，TrustMRR 实时核对", same, same)
    chk("无变化时原样返回", out == "MRR $100，TrustMRR 实时核对", repr(out))
    chk("无变化时无命中记录", hits == [], repr(hits))


def t_empty():
    """空文案不许崩。"""
    out, hits = sync("", {"mrr": 1.0}, {"mrr": 2.0})
    chk("空文案安全", out == "" and hits == [])
    out2, _ = sync(None, {"mrr": 1.0}, {"mrr": 2.0})
    chk("None 文案安全", out2 is None)


def t_label_finder():
    """标签定位本身：不该把增速/峰值认成金额。"""
    t = "近 30 天 MRR 增速 +20.6%。⚠ 历史峰值 $3,100。"
    got = SH.labelled_numbers(t)
    chk("增速里的 MRR 不被认成金额",
        not any(f == "mrr" for f, _n, _s, _e in got), repr(got))
    chk("历史峰值不被认成金额", len(got) == 0, repr(got))

    t2 = "MRR $4,251。近 30 天收入 $2,950、累计 $10,478、付费订阅 107 户。"
    got2 = SH.labelled_numbers(t2)
    fields = [f for f, _n, _s, _e in got2]
    chk("四个口径都被认出来",
        fields == ["mrr", "last_30d_revenue", "all_time", "subscriptions"],
        repr(got2))


# --------------------------------------------------------------------- 跑

def main():
    print("=" * 62)
    print("  sync_headline_numbers 测试")
    print("=" * 62)
    for title, fn in [
        ("同值陷阱（bobby 实况）", t_same_value_trap),
        ("残留错值（字段同值仍要改）", t_stale_wrong_number),
        ("历史快照语境不动", t_history_snapshot_untouched),
        ("语境误伤（增速/峰值/订阅数）", t_context_guard),
        ("普通 MRR 型", t_normal),
        ("无变化 / 空文案", t_no_change),
        ("空文案", t_empty),
        ("标签定位器", t_label_finder),
    ]:
        print("\n[%s]" % title)
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            fails.append("%s（抛异常）" % title)
            print("  [FAIL] 这一段抛异常中止了：%s: %s" % (type(e).__name__, e))

    print()
    print("=" * 62)
    if fails:
        print("  结果：%d 通过 / %d 失败" % (passed, len(fails)))
        for f in fails:
            print("    ✗ %s" % f)
    else:
        print("  结果：全部通过（%d 项）" % passed)
    print("=" * 62)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
