# -*- coding: utf-8 -*-
"""htm_increment GUI 用的 subprocess 替身：子进程输出流式进日志 + 可中止。

为什么单独一个文件（而不是塞在 htm_increment_gui.py 里）：

  1. **能单测**。htm_increment_gui.py 顶层 `import tkinter`，而很多环境
     （CI 的 ubuntu-24.04、瘦身后的容器）没有 tkinter —— 替身逻辑跟着 GUI
     一起就没法测。拆成纯 stdlib 模块后，`scripts/test_htm_increment.py`
     用任意 python 都能跑。
  2. **职责单一**。GUI 只负责「怎么显示」，替身只负责「怎么起子进程」。

## 🚨 这个替身必须「像 subprocess 模块」，而不只是「有个 run()」

2026-11-01 实测踩坑：GUI 把 `htm_increment` 模块里的 `subprocess` 整个换成
这个替身，而 10-29 那次提交给 `htm_increment._python_ok()` 新增了
`subprocess.DEVNULL` / `subprocess.SubprocessError` 这类**模块级属性**访问。
替身只有 `run()` ⇒ 必然 `AttributeError: '_ProcShim' object has no
attribute 'DEVNULL'` ⇒ GUI 点「采集」100% 崩（命令行手动跑没事，所以拖了
两天没人发现）。

所以这里做两件事：

  · `__getattr__` 把所有未定义的属性**转发给真 subprocess 模块**
    （DEVNULL / PIPE / STDOUT / SubprocessError / TimeoutExpired /
    CalledProcessError / CompletedProcess …）—— 以后 htm_increment 再用
    任何 subprocess 模块级常量都不会炸。
  · `run()` 遇到「精确指定了 stdout/stderr/capture_output/timeout」的调用
    **交回真 subprocess.run**：那些调用要的就是精确语义（输出丢弃、超时
    抛异常），替身自己实现只会把语义悄悄改掉。只有「不重定向、无限时」的
    调用（harvest 主流程那种）才走流式接管。

**双保险**：即使这个替身被换掉，htm_increment 侧的探测也已改用私有引用
`_REAL_SUBPROCESS`（见 scripts/htm_increment.py），不会再依赖可被替换的模块。
"""

import subprocess


class ProcShim:
    """替换 htm_increment 模块里的 subprocess：Popen 流式读 + 可中止。

    htm_increment 用 subprocess.run(cmd, cwd=ROOT) 起 harvest；打包后
    sys.executable 是 exe 自己不能复用（已在 htm_increment._python_exe
    处理），这里解决另一半：让子进程输出进日志窗口、停止键能杀掉它。
    """

    def __init__(self, emit, should_stop):
        self._emit = emit
        self._should_stop = should_stop
        self.proc = None

    # ---------------------------------------------------------------- 转发
    def __getattr__(self, name):
        """把模块级属性转发给真 subprocess。

        ⚠ 只在「实例上确实没有这个属性」时才会走到这里（实例属性优先），
        所以 `_emit` / `_should_stop` / `proc` 不会被误转发。

        2026-11-01 加：替身只实现 run() 时，调用方读 subprocess.DEVNULL
        会 AttributeError —— GUI 一键采集因此 100% 崩（见模块 docstring）。
        """
        return getattr(subprocess, name)

    # ---------------------------------------------------------------- 运行
    def run(self, cmd, cwd=None, **kw):
        """兼容 subprocess.run(cmd, cwd=..., **kw)。

        · 指定了 stdout/stderr/capture_output/timeout ⇒ 交回真 subprocess.run
          （要精确语义：输出丢弃 / 超时抛异常）。
        · 其它（harvest 主流程：只给 cwd）⇒ 流式接管：逐行读进日志窗口，
          停止键触发时 terminate 子进程。
        """
        precise = any(k in kw for k in
                      ("stdout", "stderr", "capture_output", "timeout", "input"))
        if precise:
            return subprocess.run(cmd, cwd=cwd, **kw)

        check = bool(kw.pop("check", False))
        if kw:
            raise TypeError("ProcShim.run 不支持这些参数：%s"
                            % ", ".join(sorted(kw)))
        self.proc = subprocess.Popen(
            [str(c) for c in cmd], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        try:
            for line in self.proc.stdout:
                self._emit(line)
                if self._should_stop():
                    try:
                        self.proc.terminate()
                    except Exception:
                        pass
                    break
        finally:
            try:
                self.proc.stdout.close()
            except Exception:
                pass
        rc = self.proc.wait()
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
        return subprocess.CompletedProcess(cmd, rc)
