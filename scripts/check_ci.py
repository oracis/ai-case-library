# -*- coding: utf-8 -*-
"""校验 .github/workflows/*.yml：YAML 结构 + 其中 shell 脚本的语法。

为什么需要这个脚本：
  1. Windows 上 `bash` 在 PATH 里解析到的是 C:\\WINDOWS\\system32\\bash.EXE ——
     那是 WSL 的启动桩，不解析脚本、对任何输入都返回 1。必须用 Git 自带的 bash。
  2. workflow 文件写错了，GitHub 只会显示「Invalid workflow file」，
     本地先验一遍能省一个来回。

用法：python scripts/check_ci.py
依赖：pyyaml（仅本地校验用，CI 本身不需要）
"""

import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 真 bash 的候选路径（按优先级）
BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Users\DELL\.workbuddy\binaries\PortableGit\versions\1.2.0\bin\bash.exe",
]

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


def find_bash():
    """找一个能真正解析脚本的 bash。绝不能是 system32 下的 WSL 桩。"""
    for p in BASH_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def main():
    try:
        import yaml
    except ImportError:
        print("[!] 缺少 pyyaml，无法校验。安装：pip install pyyaml")
        return 1

    bash = find_bash()
    if not bash:
        print("[!] 没找到可用的 bash，无法做脚本语法检查")
        return 1
    print("使用 bash: %s" % bash)
    # 自检：确认这个 bash 真的会解析脚本
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                     encoding="utf-8", newline="\n") as t:
        t.write("if then fi\n")
        bad = t.name
    rc = subprocess.run([bash, "--noprofile", "--norc", "-n", bad],
                        capture_output=True).returncode
    os.unlink(bad)
    if rc == 0:
        print("[!] 这个 bash 不报语法错（可能是 WSL 桩），校验结果不可信")
        return 1
    print("bash 自检通过（对非法脚本正确报错）\n")

    files = sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")))
    if not files:
        print("[!] 没找到 workflow 文件")
        return 1

    for path in files:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        print("=" * 62)
        print("  %s" % rel)
        print("=" * 62)

        with open(path, encoding="utf-8") as f:
            wf = yaml.safe_load(f)

        chk("YAML 能解析成 dict", isinstance(wf, dict))
        chk("有 name", bool(wf.get("name")), "-> %s" % wf.get("name"))
        on = wf.get(True) or wf.get("on")
        chk("有触发条件 on", bool(on), "-> %s" % list(on) if isinstance(on, dict) else on)

        jobs = wf.get("jobs") or {}
        chk("有 jobs", bool(jobs), "%d 个: %s" % (len(jobs), list(jobs)))

        n_scripts = 0
        for jid, job in jobs.items():
            steps = job.get("steps") or []
            matrix = (job.get("strategy") or {}).get("matrix") or {}
            print("\n  job %s  runs-on=%s  matrix=%s  steps=%d"
                  % (jid, job.get("runs-on"), matrix or "-", len(steps)))
            chk("  %s 指定了 runs-on" % jid, bool(job.get("runs-on")))
            chk("  %s 第一步是 checkout" % jid,
                "checkout" in str((steps[0] if steps else {}).get("uses", "")))
            for s in steps:
                print("      - %s" % (s.get("name") or s.get("uses")))

            for i, s in enumerate(steps):
                script = s.get("run")
                if not script:
                    continue
                n_scripts += 1
                label = "%s/step%d %s" % (jid, i + 1, (s.get("name") or "")[:26])
                with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                                 encoding="utf-8", newline="\n") as t:
                    t.write(script)
                    tmp = t.name
                p = subprocess.run([bash, "--noprofile", "--norc", "-n", tmp],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                chk("bash -n %s" % label, p.returncode == 0, (p.stderr or "").strip()[:100])
                os.unlink(tmp)
                # heredoc 结束符在 YAML 去缩进后必须顶格，否则 shell 会一直吞后续行
                if "<<" in script:
                    terms = [l for l in script.splitlines()
                             if l.strip() in ("PY", "EOF", "EOT")]
                    chk("  %s heredoc 结束符顶格" % label,
                        bool(terms) and all(l == l.strip() for l in terms))
        print("\n  共检查 %d 段 shell 脚本" % n_scripts)
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
