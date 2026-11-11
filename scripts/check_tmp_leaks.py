# -*- coding: utf-8 -*-
"""守住「修复脚本不许散失」这条线。

背景（2026-11-11）：`rewrite_yun_draft.py` 先写在 `tmp/`，修完才发现没进仓库，
又花一轮把它挪进 `scripts/`；同期 `scripts/_oneoff/` 整个被 `.gitignore` 挡着，
而 `docs/PRD.md` 却把它当「复现证据」引用 —— 换机 / 重装 / clone 之后凭据失效，
同一个修复被迫重写一遍。

四道硬闸（任一不过即非零退出）+ 一条提醒：
  1. `docs/**.md` 引用 `tmp/xxx.py`      → 脚本不在仓库里，下次必重写
  2. 引用了 `scripts/...` 但文件不存在    → 陈旧引用，读者找不到
  3. `tmp/` 下出现基线之外的新脚本/笔记    → 又开始往临时目录里写修复
  4. `scripts/` 下 `.py` 未被 git 跟踪    → 最隐蔽的一种：本地能跑，clone 就没了
  ⑤ scripts/ 或 docs/ 有未提交改动 / 未推送的提交 → 仅 WARN，不拦提交

  ⚠ ⑤ 在 pre-commit 钩子里会误报（本次提交的改动处于 staged 态），所以钩子
  传 `--staged-ok`：staged 且工作区干净的条目视为「正在提交」，不计入。

光「拦」不够（拦了还得分手搬一次）。`--archive` 是配套的「捞」：
把第 3 条命中的文件直接移进已跟踪的 `scripts/_oneoff/`，`.py` 自动把
`ROOT` 上跳层数 +1 并用新 `__file__` 校验，然后 `git add` —— 一条命令托底。

用法：
  python -X utf8 scripts/check_tmp_leaks.py                     # 全量检查
  python -X utf8 scripts/check_tmp_leaks.py --archive --dry-run # 看会归档什么
  python -X utf8 scripts/check_tmp_leaks.py --archive           # 真归档并 git add
  python -X utf8 scripts/check_tmp_leaks.py --write-baseline    # tmp 遗留文件基线
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TMP = ROOT / "tmp"
SCRIPTS = ROOT / "scripts"
ARCHIVE = SCRIPTS / "_oneoff"
BASELINE = SCRIPTS / "tmp_leaks_baseline.json"
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

# 判据：只有「丢了得重写」的脚本才硬拦（.py/.sh/.ps1，任意深度）。
# 刻意不拦的：.md（顶层 38 个里 20+ 是导入正文快照 / .old 备份，属产物；
# 拦它必然假红，而假红会让闸被无视 —— 比漏报更坏）；.txt（命令输出重定向）、
# .js（抓包 / dist 副本）、.json/.log/.png/.html（快照与产物）。
# 笔记要留档就跑 `--archive --include-notes`（显式 opt-in，只收顶层 .md）。
SCRIPT_EXTS = {".py", ".sh", ".ps1"}
NOTE_EXTS = {".md"}

RE_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./\\-]+\.py")
# 文档里的示例占位符，不是真实路径
PLACEHOLDERS = {"xxx.py", "x.py", "foo.py", "bar.py", "your_script.py", "name.py", "a.py"}
# 「把脚本从 tmp 搬到 scripts」这类叙述里允许出现 tmp 路径：必须是
# 「mv tmp/xx.py scripts/...」完整句式才算迁移记录（只写「mv tmp/」不算，
# 否则「别盲目 mv tmp/ 下的东西」会连带豁免整行的真引用）。
MIGRATION_RE = re.compile(r"mv\s+tmp/\S+\s+scripts/", re.I)


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
    """基线里允许留在 tmp/ 的历史文件（相对 tmp 的 posix 路径）。

    兼容 2026-11-11 首版只记裸文件名（`legacy_tmp_scripts`）的格式。
    """
    if not BASELINE.exists():
        return None
    try:
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names = set(data.get("legacy_tmp_files") or [])
    names |= set(data.get("legacy_tmp_scripts") or [])
    return names


def tmp_candidates(include_notes=False):
    """tmp/ 里「丢了就得重写」的文件，返回相对 tmp 的 posix 路径。

    默认只算脚本；include_notes=True 时连顶层 .md 一起（备份/快照由调用方自担）。
    """
    if not TMP.is_dir():
        return []
    out = []
    for p in TMP.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(TMP)
        ext = p.suffix.lower()
        if ext in SCRIPT_EXTS:
            out.append(rel.as_posix())
        elif include_notes and ext in NOTE_EXTS and len(rel.parts) == 1:
            out.append(rel.as_posix())
    return sorted(out)


def fresh_tmp_files(include_notes=False):
    """基线之外的新文件 —— 这些才是「下次会丢」的。基线缺失时返回全部。"""
    names = tmp_candidates(include_notes)
    base = load_baseline()
    if base is None:
        return names
    return [n for n in names if n not in base]


def write_baseline(paths):
    payload = {
        "_comment": (
            "tmp/ 里允许存在的历史遗留文件名单（2026-11-11 建档）。"
            "新增脚本 / 顶层笔记不在名单里就算泄漏 —— 跑 --archive 归档，"
            "或手工移到 scripts/ / scripts/_oneoff/。"
        ),
        "legacy_tmp_files": sorted(paths),
    }
    BASELINE.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )


def iter_doc_refs(line):
    """从一行 markdown 里取脚本路径引用，产出 (kind, token)。

    kind: 'tmp'（不该出现）/ 'scripts'（应当真实存在）。
    只认本仓库相对路径：`open-source-games/scripts/x.py` 属于别的仓库，跳过。
    """
    for token in RE_PATH_TOKEN.findall(line):
        tok = token.replace("\\", "/")
        while tok.startswith("./"):
            tok = tok[2:]
        if tok.startswith("/"):
            tok = tok[1:]
        if not tok or tok.split("/")[-1] in PLACEHOLDERS:
            continue
        if tok.startswith("tmp/"):
            if MIGRATION_RE.search(line):
                continue  # 「mv tmp/a.py scripts/a.py」式的迁移记录
            yield "tmp", tok
        elif tok.startswith("scripts/"):
            yield "scripts", tok


def check_docs(fails):
    tmp_hits, missing = [], []
    for rel, lineno, line in doc_lines():
        for kind, tok in iter_doc_refs(line):
            if kind == "tmp":
                tmp_hits.append("%s:%d  引用 %s" % (rel, lineno, tok))
            elif not (ROOT / tok).exists():
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
    fresh = fresh_tmp_files()
    if not fresh:
        return
    if load_baseline() is None:
        warns.append(
            "③ 未找到基线 %s，跳过「tmp 新增」检查（跑 --write-baseline 建档）"
            % BASELINE.relative_to(ROOT).as_posix()
        )
        return
    fails.append(
        "③ tmp/ 下新增了 %d 个未入库文件 —— tmp/ 不进仓库，写在这里等于下次重写。\n"
        "      一条命令托底：python -X utf8 scripts/check_tmp_leaks.py --archive\n"
        "      %s" % (len(fresh), "\n      ".join(fresh))
    )


# --- 归档：把 tmp 里的新文件搬进已跟踪目录 ---------------------------------
# 目录深一层 ⇒ ROOT / HERE 的上跳层数要 +1，否则 ROOT 会静默指向 scripts/
DEPTH_REWRITES = [
    (
        re.compile(
            r"^(?P<ind>[ \t]*)ROOT\s*=\s*os\.path\.dirname\(\s*"
            r"os\.path\.dirname\(\s*os\.path\.abspath\(__file__\)\s*\)\s*\)\s*$",
            re.M,
        ),
        lambda m: "%sROOT = os.path.dirname(os.path.dirname(os.path.dirname("
        "os.path.abspath(__file__))))" % m.group("ind"),
    ),
    (
        re.compile(r"^(?P<ind>[ \t]*)ROOT\s*=\s*os\.path\.dirname\(HERE\)\s*$", re.M),
        lambda m: "%sROOT = os.path.dirname(os.path.dirname(HERE))" % m.group("ind"),
    ),
    (
        re.compile(
            r"^(?P<ind>[ \t]*)ROOT\s*=\s*Path\(__file__\)\.parent\.parent\s*$", re.M
        ),
        lambda m: "%sROOT = Path(__file__).parent.parent.parent" % m.group("ind"),
    ),
]


def rewrite_depth(src):
    """上跳层数 +1，返回 (新文本, 命中条数)。"""
    hits = 0
    for pat, repl in DEPTH_REWRITES:
        src, n = pat.subn(repl, src)
        hits += n
    return src, hits


def verify_root(path):
    """用新路径当 __file__，就地 exec 头部赋值行，断言 ROOT 仍是仓库根。

    只校验真正用到 ROOT 的文件；返回 (仓库根 or None, 错误说明 or None)。
    ⚠ 实现会把头部赋值行真的 exec 一遍 —— 仅适用于本机自写的 tmp 脚本
    （--archive 的输入源），别拿去跑来路不明的文件。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if not re.search(r"\bROOT\b", text):
        return None, "未定义 ROOT"
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^(ROOT|HERE|_HERE|BASE|BASE_DIR)\s*=", s) or s.startswith(
            ("import ", "from ")
        ):
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
        # 文件里确实写了 ROOT 赋值却没算出来 ⇒ 多行写法 / 依赖被过滤，
        # 绝不能按「未定义」豁免 —— 那会让层级错的脚本静默入库
        # （2026-10-18 那批 _oneoff 脚本就是这么坏的）。
        if re.search(r"(?m)^\s*ROOT\s*=", text):
            return None, "ROOT 赋值存在但无法逐行验证（可能多行写法）"
        return None, "头部未定义 ROOT"
    got_path = Path(str(got)).resolve()
    if got_path != ROOT.resolve():
        return None, "ROOT 解析为 %s（应为仓库根）" % got_path
    return got_path, None


def _md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def _free_name(dest_dir, name):
    """目标重名时加序号 __2/__3，绝不覆盖已有归档。"""
    cand = dest_dir / name
    if not cand.exists():
        return cand
    stem, ext = cand.stem, cand.suffix
    for n in range(2, 100):
        cand = dest_dir / ("%s__%s%s" % (stem, n, ext))
        if not cand.exists():
            return cand
    raise RuntimeError("重名过多：%s" % name)


def archive_fresh(apply=False, include_notes=False):
    """把 tmp 里基线之外的文件搬进 scripts/_oneoff/ 并 git add。

    返回 (done, dups, fails) 三个清单；apply=False 时只预览不动盘。
    """
    done, dups, fails = [], [], []
    for rel in fresh_tmp_files(include_notes):
        src = TMP / rel
        if not src.exists():
            continue
        flat = rel.replace("/", "__")  # 子目录文件拍平，避免归档里再长目录
        dest = ARCHIVE / flat
        if dest.exists():
            if _md5(dest) == _md5(src):
                dups.append(rel)  # 内容一致：仓库里已有一份，可安全舍源
                if apply:
                    src.unlink()
                continue
            dest = _free_name(ARCHIVE, flat)

        if not apply:
            done.append((rel, dest.name))
            continue

        ARCHIVE.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        if dest.suffix.lower() == ".py":
            text = dest.read_text(encoding="utf-8", errors="replace")
            new, hits = rewrite_depth(text)
            if hits:
                dest.write_text(new, encoding="utf-8", newline="\n")
            _, err = verify_root(dest)
            if err and "未定义" not in err:
                # 校验不过原样退回 tmp：先把改写过的 ROOT 层级还原成原文，
                # 否则退回去的是三层写法，在 tmp/ 里反而是错的
                if hits:
                    dest.write_text(text, encoding="utf-8", newline="\n")
                shutil.move(str(dest), str(src))
                fails.append((rel, err))
                continue
        done.append((rel, dest.name))

    if apply and done:
        subprocess.run(
            ["git", "add", "--", ARCHIVE.relative_to(ROOT).as_posix()],
            cwd=str(ROOT), check=False,
        )
    return done, dups, fails


def _problematic_status(porcelain, staged_ok=False):
    """从 `git status --porcelain` 输出里挑出「不在本次提交里」的改动行。

    XY 两列：X=staged 态、Y=工作区态。
    - Y 非（空格/问号）  → 工作区还有提交之外的改动，必算
    - `??`（untracked）  → 没人管它，必算
    - 仅 staged（形如 `M `）→ staged_ok=True（pre-commit 场景）时视为
      「正在提交」跳过；False 时算未提交（--archive 之后提醒 commit 用）
    """
    out = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        x, y = line[0], line[1]
        if x == "?" and y == "?":
            out.append(line)
        elif y not in (" ", "?"):
            out.append(line)
        elif not staged_ok and x not in (" ", "?"):
            out.append(line)
    return out


def check_uncommitted(warns, staged_ok=False):
    """第 5 条：归档了但没提交 / 没推远端，等于没保住。"""
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain", "--", "scripts", "docs"],
            cwd=str(ROOT), capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.CalledProcessError):
        return
    prob = _problematic_status(st, staged_ok=staged_ok)
    if prob:
        warns.append(
            "⑤ scripts/ 或 docs/ 有 %d 处未提交改动 —— 已归档但不提交，"
            "换机 / clone 仍会丢，记得 git commit" % len(prob)
        )
        return
    try:
        ahead = subprocess.run(
            ["git", "rev-list", "--count", "@{u}..HEAD"],
            cwd=str(ROOT), capture_output=True, check=True,
        ).stdout.decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return
    if ahead and ahead.isdigit() and int(ahead) > 0:
        warns.append(
            "⑤ 本地有 %s 个提交未推送 —— 只在本机磁盘上，换机就没了" % ahead
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
                    help="把当前 tmp 里的历史文件写成基线（新增文件仍会被拦）")
    ap.add_argument("--archive", action="store_true",
                    help="把 tmp 里基线之外的新脚本/笔记移进 scripts/_oneoff/ 并 git add")
    ap.add_argument("--dry-run", action="store_true",
                    help="配 --archive 用：只报告会归档什么，不动盘")
    ap.add_argument("--include-notes", action="store_true",
                    help="归档时额外收 tmp 顶层 .md（默认只收脚本）")
    ap.add_argument("--staged-ok", action="store_true",
                    help="staged 且工作区干净的改动视为「正在提交」（pre-commit 钩子专用）")
    args = ap.parse_args()

    if args.write_baseline:
        paths = tmp_candidates(include_notes=args.include_notes)
        write_baseline(paths)
        print("已写基线 %s：%d 个历史遗留文件"
              % (BASELINE.relative_to(ROOT).as_posix(), len(paths)))
        return 0

    if args.archive:
        apply = not args.dry_run
        done, dups, fails = archive_fresh(apply=apply,
                                          include_notes=args.include_notes)
        head = "已归档" if apply else "将归档"
        for rel, dest in done:
            print("  %s %s -> scripts/_oneoff/%s" % (head, rel, dest))
        for rel in dups:
            print("  %s %s（仓库里已有同内容副本，%s）"
                  % ("清理" if apply else "可清理", rel,
                     "已删源" if apply else "加 --archive 后删源"))
        for rel, err in fails:
            print("  [FAIL] %s 无法归档：%s（已退回 tmp）" % (rel, err))
        print("\n合计：%s %d / 重复 %d / 失败 %d"
              % (head, len(done), len(dups), len(fails)))
        if not apply:
            print("（干跑，未动盘；③ 仍会红是预期的。去掉 --dry-run 真归档）")
            return 1 if fails else 0
        if done:
            print("已 git add scripts/_oneoff —— 记得 git commit，否则换机仍会丢。")
        if fails:
            return 1
        # 归档后重跑检查，确认闸转绿
        fails2, warns2 = [], []
        check_docs(fails2)
        check_tmp_new(fails2, warns2)
        check_tracked(fails2, warns2)
        check_uncommitted(warns2, staged_ok=args.staged_ok)
        for w in warns2:
            print("[WARN] " + w)
        if fails2:
            for f in fails2:
                print("[FAIL] " + f)
            return 1
        return 0

    fails, warns = [], []
    check_docs(fails)
    check_tmp_new(fails, warns)
    check_tracked(fails, warns)
    check_uncommitted(warns, staged_ok=args.staged_ok)

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
