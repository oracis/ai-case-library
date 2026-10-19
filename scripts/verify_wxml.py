# -*- coding: utf-8 -*-
"""WXML 静态校验：条件指令语法 + 配对关系。

为什么需要它：`wx:elif` 缺值这类错误**编译期才会报**，而项目里的 WXML
长期没人用开发者工具打开过 —— 2026-10-15 首次导入就炸出一处
`<view class="rev-none" wx:elif>`（属性缺值，且用错指令）。静态检查能提前抓到。

⚠ 本脚本第一版把 `wx:else` 全标成「缺值」，误报 11 处 —— `wx:else` 本身
  就是不带属性值的合法指令，只有 `wx:if` / `wx:elif` 才必须有 `=` 和值。
  第二版修掉后实测 0 误报。写这类"扫可疑模式"的脚本，**必须先验证它对
  已知正确代码不误报**，否则一堆噪音等于没检查（同一个坑：正则行首锚点
  漏抓同行的多个字段、差点得出"还有 9 列是壁垒"的反结论）。

用法：python scripts/verify_wxml.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(ROOT, "miniprogram", "pages")

passed = 0
fails = []


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s%s" % (name, ("  " + extra) if extra else ""))
    else:
        fails.append(name)
        print("  [FAIL] %s%s" % (name, ("  " + extra) if extra else ""))


def wxml_files():
    out = []
    for base, _, names in os.walk(PAGES):
        for n in names:
            if n.endswith(".wxml"):
                out.append(os.path.join(base, n))
    return sorted(out)


def strip_comments(src):
    """去掉 <!-- --> 注释：注释里的示例代码不该被当成真标签扫。"""
    return re.sub(r"<!--.*?-->", "", src, flags=re.S)


def check_directive_values(path):
    """wx:if / wx:elif 必须带 =值；wx:else 必须不带值。"""
    src = strip_comments(io.open(path, encoding="utf-8").read())
    bad = []
    for i, line in enumerate(src.splitlines(), 1):
        # wx:else 后面直接跟 > 或空格+其他属性，都合法
        if re.search(r"wx:else(?!\s*=)", line):
            # 若写成 wx:else="..." 反而不合法（多此一举）
            if re.search(r'wx:else\s*=\s*""', line):
                bad.append((i, "wx:else 不应带值", line.strip()[:60]))
            continue
        for d in ("wx:if", "wx:elif"):
            m = re.search(d + r"\s*=\s*\"([^\"]*)\"", line)
            if m and m.group(1).strip() == "":
                bad.append((i, "%s 值为空" % d, line.strip()[:60]))
            elif re.search(d + r"\b(?!\s*=)", line):
                bad.append((i, "%s 缺少 =值" % d, line.strip()[:60]))
    return bad


def check_elif_chaining(path):
    """wx:elif / wx:else 的**前一个兄弟节点**必须带 wx:if 或 wx:elif。

    前两版都错了：① 用「上一行是不是开启标签」判，多行标签就误报；
    ② 改用栈查「父节点有没有 wx:if」—— 但 wx:if/elif/else 是**兄弟**关系
    不是父子，合法写法就是
        <view wx:if="{{c}}">…</view>
        <view wx:elif="{{missing}}">…</view>
    于是把 3 处完全正常的代码判成「前面没有任何父标签」。
    ⇒ 正确做法：栈里记「前一个兄弟的条件类型」，无条件标签会把它清空。
    """
    src = strip_comments(io.open(path, encoding="utf-8").read())

    # 真实行号：记录每个 \n 之后的位置，二分查
    starts = [0]
    for m in re.finditer(r"\n", src):
        starts.append(m.end())

    def lineno(pos):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    TAG = re.compile(
        r'<(/?)([a-zA-Z][\w-]*)((?:"[^"]*"|\'[^\']*\'|[^>"\'])*?)(/?)>')
    stack = [{"name": "#root", "last_cond": None}]   # 前哨，当作根的父亲
    bad = []
    for m in TAG.finditer(src):
        closing, name, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            if len(stack) > 1:
                stack.pop()
            continue
        is_if = bool(re.search(r'wx:if\s*=\s*"[^"]+"', attrs))
        is_elif = bool(re.search(r"wx:elif\s*=", attrs))
        is_else = bool(re.search(r"wx:else(?!\s*=)", attrs))
        cond = "if" if is_if else ("elif" if is_elif else ("else" if is_else else None))

        if cond in ("elif", "else"):
            if stack[-1]["last_cond"] not in ("if", "elif"):
                bad.append((lineno(m.start()),
                            "wx:%s 前一个兄弟节点没有 wx:if/wx:elif" % cond, name))
        # 无条件标签会把连续链打断 —— 这是合法的（如 if / 普通 view / else 是反例）
        stack[-1]["last_cond"] = cond

        if not selfclose:
            stack.append({"name": name, "last_cond": None})
    return bad


def main():
    files = wxml_files()
    chk("找到 WXML 文件", len(files) >= 5, "%d 个" % len(files))
    for f in files:
        rel = os.path.relpath(f, ROOT).replace("\\", "/")
        b1 = check_directive_values(f)
        chk("%s 条件指令取值合法" % rel, not b1,
            "" if not b1 else "；".join("第%d行 %s" % (i, w) for i, w, _ in b1))
        b2 = check_elif_chaining(f)
        chk("%s wx:elif/else 配对正确" % rel, not b2,
            "" if not b2 else "；".join("第%d行 %s" % (i, w) for i, w, _ in b2))

    print("")
    if fails:
        print("[FAIL] %d 项不通过" % len(fails))
        for n in fails:
            print("  - " + n)
        return 1
    print("[OK] %d 项通过（WXML 语法与配对）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
