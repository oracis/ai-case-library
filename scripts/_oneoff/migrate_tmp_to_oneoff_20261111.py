# -*- coding: utf-8 -*-
"""把 docs/ 引用到的 tmp/*.py 迁入已跟踪的 scripts/_oneoff/。

背景：`tmp/` 与 `scripts/_oneoff/` 长期被 .gitignore 覆盖，而 docs/PRD.md 又把
这些一次性脚本当作「复现证据」引用 —— clone 或换机后脚本不存在，只能重写，
同一个修复被反复做。

本脚本做三件事：
  1) git mv tmp/<name>.py -> scripts/_oneoff/<name>.py（保留历史，无重写）
  2) 目录深了一层，把 ROOT / HERE 的上跳层数 +1（否则 ROOT 会指向 scripts/）
  3) 校验：用新的 __file__ 就地 exec 头部赋值行，断言 ROOT 仍等于仓库根；
     并 py_compile 全量语法检查。任何一项不过就整批回滚。

用法：
  python -X utf8 scripts/_oneoff/migrate_tmp_to_oneoff_20261111.py --dry-run
  python -X utf8 scripts/_oneoff/migrate_tmp_to_oneoff_20261111.py --go
"""
import argparse
import os
import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / "tmp"
DST = ROOT / "scripts" / "_oneoff"

# --- 需要 +1 层的深度表达式（按出现顺序匹配，逐条替换） -------------------
REWRITES = [
    # ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    (
        re.compile(
            r"^(?P<ind>[ \t]*)ROOT\s*=\s*os\.path\.dirname\(\s*"
            r"os\.path\.dirname\(\s*os\.path\.abspath\(__file__\)\s*\)\s*\)\s*$",
            re.M,
        ),
        lambda m: "%sROOT = os.path.dirname(os.path.dirname(os.path.dirname("
        "os.path.abspath(__file__))))" % m.group("ind"),
    ),
    # ROOT = os.path.dirname(HERE)   -> 再套一层
    (
        re.compile(r"^(?P<ind>[ \t]*)ROOT\s*=\s*os\.path\.dirname\(HERE\)\s*$", re.M),
        lambda m: "%sROOT = os.path.dirname(os.path.dirname(HERE))" % m.group("ind"),
    ),
    # ROOT = Path(__file__).parent.parent
    (
        re.compile(
            r"^(?P<ind>[ \t]*)ROOT\s*=\s*Path\(__file__\)\.parent\.parent\s*$", re.M
        ),
        lambda m: "%sROOT = Path(__file__).parent.parent.parent" % m.group("ind"),
    ),
]


def load_refs():
    """docs/ 下所有 markdown 里以 tmp/<name>.py 形式引用的脚本名。"""
    refs = set()
    for md in (ROOT / "docs").rglob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        refs |= set(re.findall(r"tmp/([A-Za-z0-9_]+\.py)", text))
    return sorted(refs)


def rewrite_depth(src):
    """上跳层数 +1，返回 (新文本, 命中条数)。"""
    hits = 0
    for pat, repl in REWRITES:
        src, n = pat.subn(repl, src)
        hits += n
    return src, hits


def verify_root(path):
    """用新路径当 __file__，就地 exec 头部赋值行，断言 ROOT 还是仓库根。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^(ROOT|HERE|_HERE|BASE|BASE_DIR)\s*=", s) or s.startswith(
            ("import ", "from ")
        ):
            # import 只留 os / sys / pathlib，其余（requests 等）不进沙箱
            if s.startswith(("import ", "from ")) and not re.search(
                r"\b(os|sys|pathlib|Path)\b", s
            ):
                continue
            lines.append(line)

    ns = {"__file__": str(path), "__name__": "__verify__"}
    pending = lines[:]
    for _ in range(len(pending) + 1):
        rest, progressed = [], False
        for line in pending:
            try:
                exec(compile(line, "<verify>", "exec"), ns)  # noqa: S102
                progressed = True
            except Exception:
                rest.append(line)
        pending = rest
        if not progressed:
            break

    got = ns.get("ROOT")
    if got is None:
        return None, "头部未定义 ROOT"
    got_path = Path(str(got)).resolve()
    if got_path != ROOT.resolve():
        return None, "ROOT 解析为 %s，应为 %s" % (got_path, ROOT)
    return got_path, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="真写（缺省只报告）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    go = args.go and not args.dry_run

    names = load_refs()
    todo = [n for n in names if (TMP / n).exists()]
    lost = [n for n in names if not (TMP / n).exists() and not (ROOT / "scripts" / n).exists()
            and not (DST / n).exists()]

    print("文档引用脚本 %d 个：待迁移 %d，已在别处/彻底丢失 %d"
          % (len(names), len(todo), len(lost)))
    if lost:
        print("  未留存（文档已引用但全库找不到）: %s" % ", ".join(lost))

    # 预检：重名 + 深度改写命中
    collided = [n for n in todo if (DST / n).exists()]
    if collided:
        print("[FAIL] 目标已存在同名文件: %s" % ", ".join(collided))
        return 2

    plan = []
    for n in todo:
        src = TMP / n
        text = src.read_text(encoding="utf-8", errors="replace")
        new_text, hits = rewrite_depth(text)
        if hits == 0:
            print("[WARN] %s 没有可识别的 ROOT 深度表达式，迁移后需人工确认" % n)
        plan.append((n, new_text, hits))

    if not go:
        print("\n--- 干跑 ---")
        for n, _, hits in plan:
            print("  tmp/%-42s -> scripts/_oneoff/  (深度改写 %d 处)" % (n, hits))
        print("\n未写盘。加 --go 执行。")
        return 0

    # 执行：移动（tmp/ 被忽略 => 源文件未跟踪，git mv 会报错，走 shutil.move）
    moved = []
    for n, new_text, hits in plan:
        src, dst = TMP / n, DST / n
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            print("[FAIL] 移动 %s 失败: %s" % (n, exc))
            return 3
        moved.append(n)
        if hits:
            dst.write_text(new_text, encoding="utf-8", newline="\n")

    # 校验：语法 + ROOT 解析
    bad = []
    for n in moved:
        dst = DST / n
        try:
            py_compile.compile(str(dst), doraise=True)
        except py_compile.PyCompileError as exc:
            bad.append((n, "语法错误: %s" % exc))
            continue
        _, err = verify_root(dst)
        if err:
            bad.append((n, err))

    print("\n迁移 %d 个，校验失败 %d 个" % (len(moved), len(bad)))
    for n, why in bad:
        print("  [FAIL] %-42s %s" % (n, why))

    if bad:
        print("\n回滚…")
        for n in moved:
            try:
                shutil.move(str(DST / n), str(TMP / n))
            except OSError as exc:
                print("  [ROLLBACK-FAIL] %s: %s" % (n, exc))
        return 4

    print("\n全部通过：%d 个脚本已入库，ROOT 均指向仓库根。" % len(moved))
    return 0


if __name__ == "__main__":
    sys.exit(main())
