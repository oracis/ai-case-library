# -*- coding: utf-8 -*-
"""htm_increment 增量管线 + GUI 替身（proc_shim）的测试。不联网。

为什么单独测这个：2026-11-01 GUI 点「采集」100% 崩，根因是
GUI 把 htm_increment 模块里的 `subprocess` 整个换成了自己的替身，
而替身只实现了 run()；htm_increment._python_ok() 要读
`subprocess.DEVNULL` / `subprocess.SubprocessError` 这些**模块级属性**
⇒ AttributeError。

这类故障最阴的地方：**命令行手动跑没事**（真 subprocess 有那些属性），
只有 GUI 这条路崩 —— 没人跑 GUI 就没人发现。所以这里把「GUI 场景」
搬进单测。

用法：
    python scripts/test_htm_increment.py
"""

import importlib.util
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

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


def section(title, fn):
    """跑一段测试，异常不让整轮中止（照 scripts/test_harvest_sources.py 的约定）。"""
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        fails.append("%s（抛异常）" % title)
        print("  [FAIL] 这一段抛异常中止了：%s: %s" % (type(e).__name__, e))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = load("_htm_inc", os.path.join(REPO, "scripts", "htm_increment.py"))
SH = load("_proc_shim", os.path.join(REPO, "gui", "proc_shim.py"))


class LegacyShim:
    """11-01 现场的替身原样：只有 run()，没有模块级属性转发。

    留这个类不是为了测它，而是为了**复现崩溃**：把 htm_increment 的
    subprocess 换成它之后，探测路径必须仍然不崩。

    崩溃原理：`subprocess.run(..., stdout=subprocess.DEVNULL)` 里
    `subprocess.DEVNULL` 是**实参求值**——先读属性再调 run。
    替身上没有 DEVNULL ⇒ AttributeError，连 run 都进不去。
    """

    def __init__(self):
        self.proc = None

    def run(self, cmd, cwd=None, **kw):
        self.proc = subprocess.Popen(
            [str(c) for c in cmd], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        out = self.proc.stdout.read()
        self.proc.wait()
        return subprocess.CompletedProcess(cmd, self.proc.returncode, out, None)


# --------------------------------------------------------------- 1. 替身形状

def t_shim_shape():
    """替身必须「像 subprocess 模块」，而不只是「有个 run()」。"""
    shim = SH.ProcShim(lambda s: None, lambda: False)

    # 11-01 的实际崩溃点：读模块级常量
    chk("替身有 DEVNULL（11-01 崩溃点）",
        getattr(shim, "DEVNULL", None) == subprocess.DEVNULL)
    chk("替身有 PIPE",
        getattr(shim, "PIPE", None) == subprocess.PIPE)
    chk("替身有 STDOUT",
        getattr(shim, "STDOUT", None) == subprocess.STDOUT)
    chk("替身有 SubprocessError（异常捕获要用）",
        getattr(shim, "SubprocessError", None) is subprocess.SubprocessError)
    chk("替身有 TimeoutExpired",
        getattr(shim, "TimeoutExpired", None) is subprocess.TimeoutExpired)
    chk("替身有 CalledProcessError",
        getattr(shim, "CalledProcessError", None) is subprocess.CalledProcessError)
    chk("替身有 CompletedProcess",
        getattr(shim, "CompletedProcess", None) is subprocess.CompletedProcess)

    # 未知属性要真抛 AttributeError，不能静默返回 None
    try:
        shim.definitely_not_a_subprocess_attr
        chk("未知属性抛 AttributeError", False, "居然没抛")
    except AttributeError:
        chk("未知属性抛 AttributeError", True)


# --------------------------------------------------------------- 2. 替身行为

def t_shim_behavior():
    """run() 的两种模式：流式接管 vs 交回真 subprocess。"""
    lines = []
    shim = SH.ProcShim(lines.append, lambda: False)

    # 流式模式（harvest 主流程：只给 cwd）
    py = sys.executable
    r = shim.run([py, "-c", "print('hello-shim')"], cwd=REPO)
    chk("流式模式 returncode == 0", r.returncode == 0, "rc=%s" % r.returncode)
    chk("流式模式输出进了 sink", any("hello-shim" in x for x in lines),
        repr(lines[:3]))

    # 精确模式（探测解释器：stdout=DEVNULL）——必须交回真 subprocess，
    # 否则输出会被塞进日志窗口（而且语义不对）。
    lines2 = []
    shim2 = SH.ProcShim(lines2.append, lambda: False)
    r2 = shim2.run([py, "-c", "import sys"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=30)
    chk("精确模式 returncode == 0", r2.returncode == 0, "rc=%s" % r2.returncode)
    chk("精确模式不往 sink 里灌输出", not lines2, repr(lines2[:3]))

    # check=True 时非零退出要抛 CalledProcessError
    try:
        shim.run([py, "-c", "raise SystemExit(3)"], cwd=REPO, check=True)
        chk("check=True 非零退出抛 CalledProcessError", False, "没抛")
    except subprocess.CalledProcessError:
        chk("check=True 非零退出抛 CalledProcessError", True)


# ------------------------------------------- 3. 核心：GUI 场景下探测不能崩

def t_probe_under_shim():
    """11-01 的原始崩溃：htm_increment.subprocess 被替身顶掉时，
    _python_ok() 不能 AttributeError。

    这里用**旧式替身**（LegacyShim：只有 run()）——那是 11-01 现场的
    真实形状。新式替身（ProcShim）自己有属性转发，撑得住，测不出这个坑；
    必须用旧式的才能钉住「依赖方不许依赖可替换模块」这条线。
    """
    for label, shim in (("旧式替身(只有 run)", LegacyShim()),
                        ("新式替身(有转发)", SH.ProcShim(lambda s: None,
                                                         lambda: False))):
        orig = H.subprocess
        try:
            H.subprocess = shim
            ok = H._python_ok(sys.executable)
            chk("[%s] _python_ok 不崩" % label, ok is True, "返回 %r" % (ok,))
        except AttributeError as e:
            chk("[%s] _python_ok 不崩" % label, False,
                "AttributeError: %s" % e)
        except Exception as e:                                 # noqa: BLE001
            chk("[%s] _python_ok 不崩" % label, False,
                "%s: %s" % (type(e).__name__, e))
        finally:
            H.subprocess = orig

    # 探测用的必须是「真 subprocess」而不是可被替换的那个
    chk("模块里存在 _REAL_SUBPROCESS 私有引用",
        getattr(H, "_REAL_SUBPROCESS", None) is subprocess)

    # windowsapps 存根必须被排除（Windows App Execution Alias）
    chk("windowsapps 存根被排除",
        H._python_ok(r"C:\Users\DELL\AppData\Local\Microsoft\WindowsApps\python.exe")
        is False)
    chk("不存在的路径被判否", H._python_ok(r"C:\nope\python.exe") is False)
    chk("None 判否", H._python_ok(None) is False)


def t_legacy_shim_really_crashes():
    """反向验证：确认 LegacyShim 确实能触发 11-01 的崩溃。

    没有这一条，上面那条可能因为「替身写错了」而假绿。
    """
    legacy = LegacyShim()
    try:
        legacy.run([sys.executable, "-c", "pass"],
                   stdout=legacy.DEVNULL)   # 旧式替身没有 DEVNULL
        chk("旧式替身确实缺 DEVNULL（现场复现成立）", False, "居然有")
    except AttributeError:
        chk("旧式替身确实缺 DEVNULL（现场复现成立）", True)


def t_python_exe_under_shim():
    """_python_exe() 在替身顶掉 subprocess 时也要能返回可用解释器。"""
    shim = LegacyShim()
    orig = H.subprocess
    orig_cache = list(H._PY_CACHE)
    try:
        H.subprocess = shim
        H._PY_CACHE.clear()
        exe = H._python_exe()
        chk("_python_exe 返回非空", bool(exe), repr(exe))
        chk("_python_exe 指向可执行文件",
            bool(exe) and os.path.isfile(exe), repr(exe))
        if exe and os.path.isfile(exe):
            r = subprocess.run([exe, "-c", "print('ok')"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=30)
            chk("_python_exe 返回的解释器真能跑", r.returncode == 0,
                "rc=%d" % r.returncode)
        chk("不是 windowsapps 存根",
            bool(exe) and "windowsapps" not in exe.lower(), repr(exe))
    except Exception as e:                                     # noqa: BLE001
        chk("_python_exe 在替身下可用", False, "%s: %s" % (type(e).__name__, e))
    finally:
        H.subprocess = orig
        H._PY_CACHE[:] = orig_cache


# --------------------------------------------- 4. 管线自身不依赖被替换的模块

def t_module_uses_real_subprocess():
    """源码级检查：htm_increment 里凡是探测用的 subprocess 调用，
    都必须走 _REAL_SUBPROCESS，不能写裸 subprocess.xxx。"""
    src = open(os.path.join(REPO, "scripts", "htm_increment.py"),
               encoding="utf-8").read()
    bad = []
    for i, ln in enumerate(src.splitlines(), 1):
        s = ln.strip()
        if s.startswith("#") or s.startswith('"'):
            continue
        if "subprocess." in s and "_REAL_SUBPROCESS" not in s:
            # 只允许 harvest 主流程那一处用可替换的 subprocess.run
            if "subprocess.run(cmd, cwd=ROOT)" not in s:
                bad.append("%d: %s" % (i, s))
    chk("探测路径不依赖可替换的模块级 subprocess", not bad,
        "；".join(bad))


def t_harvest_call_uses_replacable():
    """反过来：harvest 主流程那一处**必须**用模块级 subprocess.run
    —— GUI 靠换掉它来接管输出与中止。改了就等于 GUI 的日志窗口和
    「停止」键失效。"""
    src = open(os.path.join(REPO, "scripts", "htm_increment.py"),
               encoding="utf-8").read()
    chk("harvest 主流程仍走可替换的 subprocess.run",
        "proc = subprocess.run(cmd, cwd=ROOT)" in src)


# --------------------------------------------------------------- 5. 种子口径

def t_seed_types():
    """种子口径不许被改：收入案例/开源变现/插件 → 喂 TrustMRR；
    国内实践 → 仅登记（超本库 thesis 范围）。"""
    chk("入种子类型", H.SEED_TYPES == ("收入案例", "开源变现", "插件"),
        repr(H.SEED_TYPES))
    chk("仅登记类型", H.REGISTRY_TYPES == ("国内实践",),
        repr(H.REGISTRY_TYPES))
    chk("对比用的五张表", H.LIB_TABLES ==
        ("cases", "candidates", "inbox", "inbox_archive", "graveyard"),
        repr(H.LIB_TABLES))


# --------------------------------------------------------------- 6. 解析口径

def t_parse_index():
    """data-cards 的正则必须用 [^"]* —— 属性内引号必被转义成 &quot;，
    用 .*? 会在第一个转义引号处提前截断（10-22 踩过）。"""
    html = ('<div data-cards="[{&quot;slug&quot;:&quot;a&quot;},'
            '{&quot;slug&quot;:&quot;b&quot;}]"></div>')
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as f:
        f.write(html)
        path = f.name
    try:
        cards = H.parse_index(path)
        chk("data-cards 解析出 2 条", len(cards) == 2, repr(cards))
        chk("引号正确反转义", cards[0]["slug"] == "a", repr(cards[0]))
    finally:
        os.unlink(path)

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                     encoding="utf-8") as f:
        f.write("<div>没有 data-cards</div>")
        path = f.name
    try:
        H.parse_index(path)
        chk("缺 data-cards 时报错而不是静默返回空", False, "居然没抛")
    except SystemExit:
        chk("缺 data-cards 时报错而不是静默返回空", True)
    finally:
        os.unlink(path)


# --------------------------------------------------------------------- 跑

def main():
    print("=" * 62)
    print("  htm_increment + GUI proc_shim 测试")
    print("=" * 62)

    for title, fn in [
        ("替身形状（模块级属性转发）", t_shim_shape),
        ("替身行为（流式 / 精确两种模式）", t_shim_behavior),
        ("旧式替身确实会崩（现场复现）", t_legacy_shim_really_crashes),
        ("GUI 场景下解释器探测不崩", t_probe_under_shim),
        ("GUI 场景下 _python_exe 可用", t_python_exe_under_shim),
        ("源码级：探测不依赖可替换模块", t_module_uses_real_subprocess),
        ("源码级：harvest 仍走可替换模块", t_harvest_call_uses_replacable),
        ("种子与对比口径", t_seed_types),
        ("data-cards 解析", t_parse_index),
    ]:
        print("\n[%s]" % title)
        section(title, fn)

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
