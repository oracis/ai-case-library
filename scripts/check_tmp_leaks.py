# -*- coding: utf-8 -*-
"""守住「修复脚本不许散失」这条线。

背景（2026-11-11）：`rewrite_yun_draft.py` 先写在 `tmp/`，修完才发现没进仓库，
又花一轮把它挪进 `scripts/`；同期 `scripts/_oneoff/` 整个被 `.gitignore` 挡着，
而 `docs/PRD.md` 却把它当「复现证据」引用 —— 换机 / 重装 / clone 之后凭据失效，
同一个修复被迫重写一遍。

四道检查（任一不过即非零退出）：
  1. `docs/**.md` 引用 `tmp/xxx.py`      → 脚本不在仓库里，下次必重写
  2. 引用了 `scripts/...` 但文件不存在    → 陈旧引用，读者找不到
  3. `tmp/` 下出现基线之外的新 `.py`      → 又开始往临时目录里写修复
  4. `scripts/` 下 `.py` 未被 git 跟踪    → 最隐蔽的一种：本地能跑，clone 就没了

用法：
  python -X utf8 scripts/check_tmp_leaks.py                # 全量检查
  python -X utf8 scripts/check_tmp_leaks.py --write-baseline   # tmp 遗留脚本入库基线
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TMP = ROOT / "tmp"
SCRIPTS = ROOT / "scripts"
BASELINE = SCRIPTS / "tmp_leaks_baseline.json"
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

RE_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./\\-]+\.py")
# 文档里的示例占位符，不是真实路径
PLACEHOLDERS = {"xxx.py", "foo.py", "bar.py", "your_script.py", "name.py", "a.py"}
# 「把脚本从 tmp 搬到 scripts」这类叙述里允许出现 tmp 路径：写成了 mv 句式即视为迁移记录
MIGRATION_MARK = "mv tmp/"


def git_tracked(prefixes):
    """返回 git 已跟踪文件的相对路径集合（posix 分隔符）。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--"] + list(prefixes),
            cwd=str(ROOT), capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return None  # 不在 git 仓库里 / 没有 git，交给调用方降级
    return {p.replace("\\", "/") for p in out.split("\0") if p}


def doc_lines():
    for md in sorted(DOCS.rglob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            yield md.relative_to(ROOT).as_posix(), i, line


def load_baseline():
    if not BASELINE.exists():
        return None
    try:
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return set(data.get("legacy_tmp_scripts") or [])


def tmp_scripts():
    if not TMP.is_dir():
        return []
    return sorted(p.name for p in TMP.glob("*.py"))


def write_baseline(names):
    payload = {
        "_comment": (
            "tmp/ 里允许存在的历史遗留脚本名单（2026-11-11 建档）。"
            "新增 .py 不在名单里就算泄漏，必须移到 scripts/ 或 scripts/_oneoff/。"
        ),
        "legacy_tmp_scripts": sorted(names),
    }
    BASELINE.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def check_docs(fails):
    tmp_hits, missing = [], []
    for rel, lineno, line in doc_lines():
        for token in RE_PATH_TOKEN.findall(line):
            tok = token.replace("\\", "/").lstrip("./")
            if not tok or tok.split("/")[-1] in PLACEHOLDERS:
                continue
            # 只认本仓库的相对路径；`open-source-games/scripts/x.py` 属于别的仓库
            if tok.startswith("tmp/"):
                if MIGRATION_MARK in line:
                    continue  # 「mv tmp/a.py scripts/a.py」式的迁移记录
                tmp_hits.append("%s:%d  引用 %s" % (rel, lineno, tok))
            elif tok.startswith("scripts/"):
                if not (ROOT / tok).exists():
                    missing.append("%s:%d  引用 %s（不存在）" % (rel, lineno, tok))
    if tmp_hits:
        fails.append(
            "① docs 仍在引用 tmp/ 脚本（%d 处）—— 这些脚本不在仓库里，"
            "下次只能重写：\n      %s" % (len(tmp_hits), "\n      ".join(tmp_hits))
        )
    if missing:
        fails.append(
            "② docs 引用了不存在的 scripts 文件（%d 处）：\n      %s"
            % (len(missing), "\n      ".join(missing))
        )


def check_tmp_new(fails, warns):
    names = tmp_scripts()
    base = load_baseline()
    if base is None:
        warns.append(
            "③ 未找到基线 %s，跳过「tmp 新增脚本」检查（跑 --write-baseline 建档）"
            % BASELINE.relative_to(ROOT).as_posix()
        )
        return
    fresh = [n for n in names if n not in base]
    if fresh:
        fails.append(
            "③ tmp/ 下新增了 %d 个未入库脚本 —— tmp/ 不进仓库，写在这里等于下次重写。"
            "请移到 scripts/_oneoff/（一次性）或 scripts/（常驻）：\n      %s"
            % (len(fresh), "\n      ".join(fresh))
        )


def check_tracked(fails, warns):
    tracked = git_tracked(["scripts", "docs"])
    if tracked is None:
        warns.append("④ 拿不到 git 跟踪清单（不在仓库或无 git），跳过跟踪检查")
        return
    untracked = []
    for p in sorted(SCRIPTS.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel not in tracked:
            untracked.append(rel)
    if untracked:
        fails.append(
            "④ scripts/ 下 %d 个 .py 未被 git 跟踪 —— 本地能跑、clone 就没了"
            "（多半被 .gitignore 挡了，或忘了 git add）：\n      %s"
            % (len(untracked), "\n      ".join(untracked))
        )


def main():
    ap = argparse.ArgumentParser(description="检查脚本是否散落在仓库外")
    ap.add_argument("--write-baseline", action="store_true",
                    help="把当前 tmp/*.py 写成遗留基线（新增文件仍会被拦）")
    args = ap.parse_args()

    if args.write_baseline:
        names = tmp_scripts()
        write_baseline(names)
        print("已写基线 %s：%d 个遗留脚本"
              % (BASELINE.relative_to(ROOT).as_posix(), len(names)))
        return 0

    fails, warns = [], []
    check_docs(fails)
    check_tmp_new(fails, warns)
    check_tracked(fails, warns)

    for w in warns:
        print("[WARN] " + w)
    if fails:
        for f in fails:
            print("[FAIL] " + f)
        print("\n结论：FAIL（%d 项）。修复脚本必须进仓库，不然下次还得重写。" % len(fails))
        return 1

    print("[OK] 脚本都在仓库里：docs 无 tmp 引用、引用可解析、"
          "tmp 无新增脚本、scripts 全部被 git 跟踪。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
