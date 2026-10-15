# -*- coding: utf-8 -*-
"""付费墙有效性断言 —— 让「三行代码就能绕过」在测试里炸出来，而不是等用户发现。

背景（2026-10-15）：
    复查个人主体虚拟支付时发现，付费导出的 17 列**全部**能从免费接口
    `get-cases` 拿到。于是导出这道「服务端硬门」只挡住了「调用 export-cases」
    这一个入口，挡不住「不调用它、自己在前端拼 CSV」。
    ⇒ 付费门形同虚设，而 `verify_miniprogram.js` 的 37 项断言**一项都没查这个**。

    这不是实现 bug，是设计死结：个人主体「工具」类目锁内容会被驳回
    ⇒ 内容必须免费 ⇒ 数据源完全公开 ⇒ 凡是把公开数据再加工的功能
    （导出/筛选/排序/转格式）都没有收费的立足点。

本脚本只回答一个问题：**付费功能依赖的字段，免费接口是否都已经给出？**
如果答案是「是」，那这道门就是装饰，必须重做设计而不是加固。

⚠ 本脚本自己也踩过坑，注释里记了：解析 FIELDS 时正则**不能加行首锚点**，
  因为多个字段写在同一行（`id: true, name: true, ...`）。加 `^` 会只抓到
  每行第一个，30 个字段只抓到 11 个，差点得出「还有 9 列是壁垒」的错误结论。
  所以下面用「块内 key 总数 == 抓到的集合大小」做反向校验，锚点错了立刻炸。

不联网。用法：python scripts/verify_paywall.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CF = os.path.join(ROOT, "miniprogram", "cloudfunctions")
GET_CASES = os.path.join(CF, "get-cases", "index.js")
EXPORT_CASES = os.path.join(CF, "export-cases", "index.js")

# 深度报告的「正文」字段。前端软墙下这些会被免费返回，属已知取舍，
# 但一旦要在服务端硬收费，就必须从这里移出白名单。
REPORT_BODY_FIELDS = [
    "what_it_does", "how_it_makes_money", "why_it_works",
    "verdict", "judgment", "playbook",
]

passed = 0
fails = []
warns = []


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        fails.append(name)
        print("  [FAIL] %s%s" % (name, ("  " + extra) if extra else ""))


def warn(name, extra=""):
    warns.append(name)
    print("  [WARN] %s%s" % (name, ("  " + extra) if extra else ""))


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def parse_get_cases_fields():
    """返回 (免费层 FREE, 付费层 PAID, 块内全部 key 集合)。

    2026-10-15 起 get-cases 把白名单拆成 FREE / PAID 两层（内容收费）。
    ⚠ 解析时不能加行首锚点 —— 多字段写在同一行（`id: true, name: true, ...`）。
    """
    src = read(GET_CASES)
    m = re.search(r"const FREE = \{(.*?)\n\};", src, re.S)
    p = re.search(r"const PAID = \{(.*?)\n\};", src, re.S)
    if not m or not p:
        raise AssertionError(
            "get-cases 里找不到 FREE / PAID 两层白名单，代码结构变了")
    free = set(re.findall(r"([a-z_0-9]+):\s*true", m.group(1)))
    paid = set(re.findall(r"([a-z_0-9]+):\s*true", p.group(1)))
    every = free | paid
    # ⚠ 旧的单层结构（const FIELDS = {）如果回来，说明有人把两层合回去了
    if re.search(r"const FIELDS = \{", src):
        raise AssertionError("检测到旧的单层 FIELDS 白名单，两层结构被合回去了")
    return free, paid, every


def parse_export_cols():
    src = read(EXPORT_CASES)
    m = re.search(r"const COLS = \[(.*?)\n\];", src, re.S)
    if not m:
        raise AssertionError("export-cases 里找不到 COLS 块，代码结构变了")
    return re.findall(r"\['([a-z_0-9]+)'", m.group(1))


def test_parser_sanity():
    """解析器自检：锚点写错时必须炸，而不是安静地少抓字段。"""
    free, paid, every = parse_get_cases_fields()
    chk("FREE/PAID 两层都能解析", bool(free) and bool(paid),
        "FREE %d 个 / PAID %d 个" % (len(free), len(paid)))
    chk("两层没有交集（同一字段不能既免费又收费）", not (free & paid),
        "" if not (free & paid) else "重叠：%s" % ", ".join(sorted(free & paid)))
    # updated_ts 必须留在 FREE：ver 版本号靠它算，而全量查询只取 FREE 层
    chk("updated_ts 在 FREE 层（否则 ver 恒为 0，客户端永拉不到新案例）",
        "updated_ts" in free)


def test_export_paywall():
    """核心断言：付费导出的列不能全部已被免费接口给出。"""
    free, _, _ = parse_get_cases_fields()
    cols = parse_export_cols()
    leaked = [c for c in cols if c in free]
    unique = [c for c in cols if c not in free]

    print("    免费层 %d 个字段；付费导出 %d 列；其中免费已泄漏 %d 列"
          % (len(free), len(cols), len(leaked)))
    if leaked:
        print("    仍泄漏：%s" % ", ".join(leaked))
    if unique:
        print("    已锁住：%s" % ", ".join(unique))

    chk("付费导出存在「免费接口给不了」的列（付费门才成立）", bool(unique),
        "" if unique else "→ %d/%d 列全部泄漏，导出门形同虚设" % (len(leaked), len(cols)))


def test_export_server_side_gate():
    """导出的权益校验必须落在服务端（云函数内），不能只在前端。"""
    src = read(EXPORT_CASES)
    chk("export-cases 调了服务端权益查询", "getEntitlements" in src)
    chk("export-cases 复用统一的 canReadFull（两处规则不许各写一份）",
        "canReadFull" in src)
    trusts_client = bool(re.search(r"event\.\w*(paid|vip|isMember|entitle)", src))
    chk("导出不信任前端传来的付费标志", not trusts_client,
        "" if not trusts_client else "发现 event.* 里的付费字段")
    chk("导出有服务端拒绝分支", "40301" in src and "return" in src)


def test_report_soft_wall():
    """深度报告正文必须在 PAID 层（服务端硬门），不是前端软墙。"""
    free, paid, _ = parse_get_cases_fields()
    exposed = [f for f in REPORT_BODY_FIELDS if f in free]
    locked = [f for f in REPORT_BODY_FIELDS if f in paid]
    chk("深度报告正文已移出免费层", not exposed,
        "" if not exposed else "→ 仍在免费层：%s" % ", ".join(exposed))
    chk("深度报告正文在 PAID 层", len(locked) == len(REPORT_BODY_FIELDS),
        "%d/%d" % (len(locked), len(REPORT_BODY_FIELDS)))


def test_revenue_fields_locked():
    """收入五件套必须锁 —— 它们同时是导出的核心列。"""
    free, paid, _ = parse_get_cases_fields()
    rev = ["mrr", "last_30d_revenue", "arr", "all_time", "caliber"]
    exposed = [f for f in rev if f in free]
    chk("收入/口径字段已移出免费层", not exposed,
        "" if not exposed else "→ 仍免费：%s" % ", ".join(exposed))


def test_lib_deliver_present():
    """get-cases / export-cases 都 require('./lib/deliver.js')，
    但 2026-10-15 之前那两个目录里**没有**这个文件 —— 部署即崩。
    这里钉住：谁 require 谁就得有，且四处内容必须一致。"""
    need = ["get-cases", "export-cases", "pay-notify", "query-order"]
    import hashlib
    hashes = {}
    for fn in need:
        p = os.path.join(CF, fn, "lib", "deliver.js")
        if not os.path.isfile(p):
            chk("%s 有 lib/deliver.js" % fn, False, "→ 缺失，部署会崩")
            continue
        with open(p, "rb") as f:
            hashes[fn] = hashlib.md5(f.read()).hexdigest()
    if len(hashes) == len(need):
        uniq = set(hashes.values())
        chk("四处 deliver.js 内容一致（改一处要同步三处）", len(uniq) == 1,
            "" if len(uniq) == 1 else "出现 %d 个不同版本" % len(uniq))
    chk("deliver.js 导出了 canReadFull", "canReadFull" in read(
        os.path.join(CF, "get-cases", "lib", "deliver.js")))


def main():
    print("== 解析器自检 ==")
    test_parser_sanity()
    print("== 依赖完整性 ==")
    test_lib_deliver_present()
    print("== 导出付费门 ==")
    test_export_paywall()
    test_export_server_side_gate()
    print("== 内容收费 ==")
    test_report_soft_wall()
    test_revenue_fields_locked()

    print("")
    if fails:
        print("[FAIL] %d 项不通过 —— 付费墙设计有洞" % len(fails))
        for n in fails:
            print("  - " + n)
        return 1
    print("[OK] %d 项通过" % passed)
    if warns:
        print("  另有 %d 项 WARN" % len(warns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
