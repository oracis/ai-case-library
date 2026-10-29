# -*- coding: utf-8 -*-
"""howtomakemi 增量管线 GUI（tkinter 单文件，只用标准库）。

把 scripts/htm_increment.py 的一键 CLI 包上一层窗口：选项目目录/日期/选项，
点「开始跑」，日志实时滚动，跑完直接给简报路径和一键打开。中途可停（会连带
中止 harvest 子进程）。

为什么是 in-process 而不是 subprocess 调 CLI：
  PyInstaller 打包后 sys.executable 是 exe 自己，再拿它起子进程会把 GUI
  重新拉起来；而且子进程输出写的是 OS 级 stdout，--noconsole 下全丢。
  所以 GUI 用 importlib 加载真的 htm_increment.py 原地跑，并把它的
  subprocess.run 换成一个可流式、可中止的 Popen 壳。

用法：
    python gui/htm_increment_gui.py              # 开窗口
    python gui/htm_increment_gui.py --selftest   # 无窗口自检（打包后验证 exe）
    ... --selftest --full                        # 自检也真跑采集（默认 dry-run）

打包：
    python -X utf8 gui/build_exe.py              # → gui/dist/htm-increment-gui.exe

项目目录定位（按优先级）：
    1. 上次保存的配置（%APPDATA%\\htm-increment-gui\\config.json）
    2. 从 exe/脚本所在目录逐级向上，找含 scripts/harvest.py + data/cases.json 的目录
    3. 内置默认 D:\\ai-case-library
"""

import argparse
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime

# 预导入（PyInstaller 静态分析盲区，2026-10-23 实测踩过）：
# htm_increment.py / text_clean.py 是运行时 importlib 动态加载的，打包时
# 分析不到它们的 import；不在这里先导一遍，冻结态 import 直接
# ModuleNotFoundError: Standard library module 'html' was not found。
import html as _html  # noqa: E402,F401  htm_increment 解析 data-cards 用
import re as _re  # noqa: E402,F401  htm_increment + text_clean 用
import shutil as _shutil  # noqa: E402,F401  htm_increment._python_exe 冻结态用
import urllib.request as _urllib_request  # noqa: E402,F401  htm_increment 抓首页用

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_NAME = "htm-increment-gui"
DEFAULT_ROOT = r"D:\ai-case-library"
DEFAULT_OUT = "tmp/htm_screen"
DEFAULT_VERIFIED = "tmp/htm_screen/verify_results.json"
UI_FONT = ("Microsoft YaHei", 9)
MONO_FONT = ("Consolas", 9)

# 窗口图标（标题栏/任务栏用）。⚠ PyInstaller 的 --icon 只设 **exe 文件**
# 图标；Tk 会用它自己的默认「蓝羽毛」盖掉运行窗口的标题栏图标，必须显式
# iconphoto。这里内嵌 zero-dep-icon-gen 生成的 16/32/48 PNG（base64），
# 不依赖外部文件——exe 挪到哪图标都跟着。换图标：改这三串 + 重打包。
_ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABO0lEQVR42p3T20oCURQG4PVocxlBhJUkoiWGncSwFLETFdIzdNMrhIEWBME8RV1k15rmodOMdIC/vawl2z1aTf/t8O211t6ziFQCY1Vrarxqz0zUEJysYTZwh9B0HeFgHZFQA9FwA/ORe8TmmojHmliIt+xEomWRhuEDQ2EsLrWxvNK2yGdlHWM12bHJxIX9Dq6v3iA5LTqjMJKpB5CJJcUTp4clw3AqrQ7Q25bKZtucUsn14LWNR5A+s1Q2Z2bMMXE6qw7QL2zUzOVyt/fNxOu5J5B+2zKzjiVn510PzuSfQeZTSbhtqcy5qbx7cHZLHTDsnWVmqcyYU7n9GMC53RfQDz/JwMyMOReXr32c31MH/AVL24yPjt0+3jxwQN+L8SvW2xa8XXBt4q36J8bOofu1kbxVvBh+Kgv+BMNz1WRxTG4TAAAAAElFTkSuQmCC",
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACZElEQVR42sWX2U5TURSG96Nxq8YBKMhQRqGMRQoFChYtCkETboyPoQhI8U1EWqqXLS0dADskPU1+94o5ZR/sPmdVKedPdtKLk3z/XtPuEuKGOu4nI50PkvA8TMHzKIWuxyl0PznD0/Yz9HTI05lGryeNvq40+rvT8PZk4O3NYKAvg8H+cwx55Rk4x/BgFiNDWYwOZ/FsRJ7RHMbGchGhU/u9ZJuEo4VwjI/n4PPl4ZvIt7kJx8RkHpNTigkX4JiaLuAucq6FT8/IM1uICBfhmPFfQHDhmxsFfP5UwvfjKkzR7/290j/DZ+fIAAO++7EIJx3sl5uG++elASf48bcquDo5MZqC+wOXEM3cfG+3ZMn5u7eXEmo1uLNzxYbPLUgDdjlXtb11oS24Lwdly7dc+PNFaUBX7VRwups3KrhYzKh/H41WWPD5IBnQtJpa7ZxWOzy8jkL0qMKCB5auIHR9rorb56bicYMFDyxLA7oho4rb56oBDnxhRRrQTTg1BVTtTnAKu6mjrxUWfDH0C0I3XmnCXfd3VQuPxY2/5sH7D0UWPLhKBmxmuypqNbuwq+LCg2vSgN3DQuNVFbUaVXujsNfzf2qw4UvhIoTTq0bjtVmdJgwWfHldGuA8qTRenUQ3t5qoOcJXXpKBJt5zmnAUdmozOlTtasHRzVUlftRs4aFX0sD//Jlo1Gp085smdPBQpARxm3Az5wRVpYOvbkgDtw03c26aSPysaeFrr6WBVsDtcq7CX7wpSwMuwsOb0gCtS27Bw1vlP6uaS3DUNyPa1e4avr5dse6HtKu5BldF61LLc67oN6GWQux3syAtAAAAAElFTkSuQmCC",
    "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAADfUlEQVR42tWZWVMTURCF56fx7Iay7/u+74RNg2wKBWr5NyigFLS0tPgXGiC+yp6ELSkIVW2fJANh5ibcuTOYzKk6j0l9J+nbt2da01Io79Ffb/5j9pNdKngad+Ezdu4uFeXuUdHzPSp+wc7boxI4f59KC9iF+1QGF+1TefEBVZTEXVnKLjugqrJDqio/pOoKduUh1cBVR1Rbza45ojq49ojq645jbqg/9jY0HHs1WTF4DpsYnrIAnhieGhvhADU1BXLug9/MYnhqag5Qc0tg083wMbe0GkJkedkY4am1LQjnJAdwGzy1tQcpudu4Dr6tI0jtnUGvliWtUgWeHeIA7oWnjq4QaU7AryxfxJys37+uaHXlgt7Mhh4MvqM7EUAVfmX5nGS1thZ2HL6zhwOowE9PBklFPt8Vzc+fOAYfD/Cf4JO1sHDqCHxX7wlpTsKj5tdWb51OTsB39SGAhZoXCYd1biaUstvMvz0Rfm5rK2obvhsB7BxYwMu0ygWue58vavr84uKpLfjufgSQbJWq8PqBRd2L/gU78D0DHEC2zxuVrmxStcpPn8Om77ED3zN4ygEkblhRANUb1qj1jYgyfC8CyIwHom6jOh4IAyjC9w4hgMRsY7pVuUWqjgfr65E737XBAVTh+4Y5gMxgJgygONuYAnyJKMP3eRBAYqpExzEGUB3MjEIAVfh+zxlpMiMxat4oFfh378/MAb5GlOH7RziAzDyPkdgo3LAy8EtLt9Db2+bLzA78wGgigMzDiHAow2R5zy+fTts7UVvwsQCyT1KY583jcTQxWcrXfLI+fDy3BT84hgAWHgMxz4uEGzZVzYvKRpdd+KHxc9KsPMPiYeQ+oU0aW2Uq7fijtuCHJhDA4gO4aCizox3/tTL8MAKovj3AJOlkCBX44ZcIYOPVB+Z5mSDoNvqBRdkIQ/y5tgzvecUBnHpvg6EMxmyD2zXmFJcUfnGR/BzCCrzHe8EBHvClU7pLKl0IWfgRBMgEvH5gUTYifftxKQU/MokAGYLXD6xfEOL7z0sp+NHXHCCT8PqBNYZAABn40SkEyDC8fmBRNgC3Aj82FSYtG+BlD6wRfmw6EcCt8OMzHCCxh3UlPNurYYnsUniamA3HF+AuhaebLSU24G6Dn5iL3N3aYwPuInjxth4bcNfC68IG3DVlk05YImMPmw2t8qbbCPQPWONrgLXFlwoAAAAASUVORK5CYII=",
)

RC_TEXT = {
    0: "完成（含「本次无新增」）",
    1: "失败：抓取或解析出错，看日志里的 [!] 行",
    2: "失败：采集子进程出错，看日志里的 [!] 行",
}


# ---------------------------------------------------------------- 基础工具

def app_dir():
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def load_config():
    p = os.path.join(app_dir(), "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    p = os.path.join(app_dir(), "config.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def self_path():
    """exe 里用 sys.executable；开发态用 __file__。"""
    return sys.executable if getattr(sys, "frozen", False) else __file__


def find_project_root(start=None):
    """从 exe/脚本所在目录逐级向上，找含 scripts/harvest.py + data/cases.json 的目录。"""
    start = os.path.abspath(start or self_path())
    d = os.path.dirname(start)
    for _ in range(6):
        if (os.path.isfile(os.path.join(d, "scripts", "harvest.py"))
                and os.path.isfile(os.path.join(d, "data", "cases.json"))):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def resolve_root(cfg=None):
    cfg = cfg or {}
    r = (cfg.get("root") or "").strip()
    if r and os.path.isfile(os.path.join(r, "scripts", "harvest.py")):
        return r
    return find_project_root() or DEFAULT_ROOT


def build_namespace(root, date, out, verified, skip_fetch, dry_run):
    """按 htm_increment.run_increment 需要的字段造 Namespace。"""
    return argparse.Namespace(
        date=(date or "").strip() or None,
        out=(out or "").strip() or DEFAULT_OUT,
        verified=(verified or "").strip() or None,
        skip_fetch=bool(skip_fetch),
        dry_run=bool(dry_run),
    )


def load_increment_module(root):
    """importlib 加载真的 scripts/htm_increment.py（htm_increment 自己会把
    scripts/ 加进 sys.path，text_clean 随之可导入）。"""
    scripts_dir = os.path.join(root, "scripts")
    path = os.path.join(scripts_dir, "htm_increment.py")
    if not os.path.isfile(path):
        raise FileNotFoundError("找不到 %s" % path)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("_htm_increment_loaded", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _LogSink:
    """接管 sys.stdout：按行汇入队列；selftest 模式同时回显到真 stdout。"""

    def __init__(self, q=None, echo=False):
        self.q = q
        self.echo = echo
        self._buf = ""
        self._real = sys.stdout

    def emit(self, text):
        if self.echo and self._real:
            self._real.write(text)
            self._real.flush()
        if self.q is None:
            return
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.q.put(("line", line.rstrip("\r")))

    def write(self, s):
        self.emit(s)

    def flush(self):
        pass


class _ProcShim:
    """替换 htm_increment 模块里的 subprocess：Popen 流式读 + 可中止。

    htm_increment 用 subprocess.run(cmd, cwd=ROOT) 起 harvest；打包后
    sys.executable 是 exe 自己不能复用（已在 htm_increment._python_exe
    处理），这里解决另一半：让子进程输出进日志窗口、停止键能杀掉它。
    """

    def __init__(self, emit, should_stop):
        self._emit = emit
        self._should_stop = should_stop
        self.proc = None

    def run(self, cmd, cwd=None, **kw):
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
        return subprocess.CompletedProcess(cmd, rc)


# ---------------------------------------------------------------- 自检模式

def run_selftest(argv):
    ap = argparse.ArgumentParser(description="htm_increment GUI 无窗口自检")
    ap.add_argument("--root", default=None, help="项目根目录（默认自动定位）")
    ap.add_argument("--date", default=None)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--verified", default=DEFAULT_VERIFIED)
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--full", action="store_true", help="真跑采集（默认 dry-run）")
    args = ap.parse_args(argv)

    root = (args.root or "").strip() or resolve_root()
    date_label = (args.date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    home = os.path.join(root, args.out, "home_%s.html" % date_label)
    skip = args.skip_fetch or os.path.exists(home)
    if not skip:
        print("[selftest] 没有 --skip-fetch 且找不到 %s，将真联网抓首页。" % home)

    ns = build_namespace(root, args.date, args.out, args.verified,
                         skip_fetch=skip, dry_run=not args.full)
    print("[selftest] 项目目录：%s" % root)
    print("[selftest] 日期 %s｜skip-fetch=%s｜dry-run=%s"
          % (ns.date or date_label, ns.skip_fetch, ns.dry_run))

    sink = _LogSink(echo=True)
    old = sys.stdout
    sys.stdout = sink
    rc = 1
    try:
        mod = load_increment_module(root)
        stop = threading.Event()
        mod.subprocess = _ProcShim(sink.emit, stop.is_set)
        rc = mod.run_increment(ns)
    except Exception:
        sys.stdout = old
        traceback.print_exc()
        rc = 1
    finally:
        sys.stdout = old
    print("[selftest] 退出码 %d（%s）" % (rc, RC_TEXT.get(rc, "?")))
    return rc


# ---------------------------------------------------------------- GUI

class IncrementApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("howtomakemi 增量抓取")
        self.geometry("820x620")
        self.minsize(640, 480)

        cfg = load_config()
        root = resolve_root(cfg)
        self.var_root = tk.StringVar(value=root)
        self.var_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.var_out = tk.StringVar(value=cfg.get("out") or DEFAULT_OUT)
        self.var_verified = tk.StringVar(value=cfg.get("verified") or DEFAULT_VERIFIED)
        self.var_skip = tk.BooleanVar(value=False)
        self.var_dry = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="就绪")
        self.var_report = tk.StringVar(value="")

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False

        self._build_ui()
        self._apply_icon()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _apply_icon(self):
        """把内嵌 PNG 设成窗口图标（标题栏 + 任务栏）。

        不设置的话 Tk 用它默认的「蓝羽毛」——PyInstaller --icon 只管 exe
        文件本身，管不到运行中的窗口。PhotoImage 引用要挂在 self 上，
        否则被垃圾回收后图标变回默认。
        """
        try:
            imgs = [tk.PhotoImage(data=b64, format="png")
                    for b64 in _ICON_PNG_B64 if b64]
            if imgs:
                self.iconphoto(True, *imgs)
                self._icon_imgs = imgs
        except Exception:
            pass  # 图标失败不影响功能

    # ---- 界面

    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="项目目录", font=UI_FONT).grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_root, font=UI_FONT).grid(
            row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(top, text="浏览…", command=self._pick_root).grid(row=0, column=2)
        ttk.Button(top, text="自动探测", command=self._auto_root).grid(row=0, column=3)

        ttk.Label(top, text="日期标签", font=UI_FONT).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(top, textvariable=self.var_date, width=14, font=UI_FONT).grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(6, 0))
        ttk.Label(top, text="留空按今天；决定简报/快照文件名的后缀",
                  foreground="#6B7280", font=UI_FONT).grid(
            row=1, column=1, sticky="w", padx=(120, 0), pady=(6, 0))

        ttk.Label(top, text="台账目录", font=UI_FONT).grid(row=2, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_out, font=UI_FONT).grid(
            row=2, column=1, sticky="ew", padx=(4, 4))
        ttk.Label(top, text="核验台账", font=UI_FONT).grid(row=3, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_verified, font=UI_FONT).grid(
            row=3, column=1, sticky="ew", padx=(4, 4))

        opts = ttk.Frame(self)
        opts.pack(fill="x", **pad)
        ttk.Checkbutton(opts, text="跳过抓取（复用已存的首页 HTML）",
                        variable=self.var_skip).pack(side="left")
        ttk.Checkbutton(opts, text="预演（只解析对比，不跑采集）",
                        variable=self.var_dry).pack(side="left", padx=(16, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.btn_start = ttk.Button(btns, text="开始跑", command=self._on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="停止", command=self._on_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))

        self.log_text = ScrolledText(self, state="disabled", wrap="char",
                                     font=MONO_FONT, background="#FFFFFF",
                                     foreground="#1F2937")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        self.log_text.tag_config("err", foreground="#B3261E")
        self.log_text.tag_config("info", foreground="#1A56DB")
        self.log_text.tag_config("dim", foreground="#6B7280")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", side="bottom", **pad)
        ttk.Label(bottom, text="状态：", font=UI_FONT).pack(side="left")
        ttk.Label(bottom, textvariable=self.var_status, font=UI_FONT,
                  foreground="#1A56DB").pack(side="left")
        self.btn_report = ttk.Button(bottom, text="打开简报", state="disabled",
                                     command=self._open_report)
        self.btn_report.pack(side="right")
        self.btn_dir = ttk.Button(bottom, text="打开台账目录", state="disabled",
                                  command=self._open_dir)
        self.btn_dir.pack(side="right", padx=(0, 8))
        ttk.Label(bottom, textvariable=self.var_report, foreground="#6B7280",
                  font=MONO_FONT).pack(side="right", padx=(0, 12))

    # ---- 事件

    def _pick_root(self):
        d = filedialog.askdirectory(initialdir=self.var_root.get() or DEFAULT_ROOT)
        if d:
            self.var_root.set(d)

    def _auto_root(self):
        r = find_project_root()
        if r:
            self.var_root.set(r)
            self.var_status.set("已自动定位到 %s" % r)
        else:
            messagebox.showinfo("自动探测", "从 exe 所在目录向上没找到项目根，"
                                           "请手动「浏览…」选择 ai-case-library 目录。")

    def _on_start(self):
        root = self.var_root.get().strip()
        if not os.path.isfile(os.path.join(root, "scripts", "harvest.py")):
            messagebox.showerror(
                "项目目录不对",
                "目录下找不到 scripts/harvest.py：\n%s\n\n请指向 ai-case-library 仓库根目录。"
                % root)
            return
        save_config({"root": root,
                     "out": self.var_out.get().strip(),
                     "verified": self.var_verified.get().strip()})
        # Tk 变量只能在主线程读（tkinter 非线程安全，工作线程里 .get() 会抛
        # RuntimeError: main thread is not in main loop）——先取值再传下去。
        params = {
            "root": root,
            "date": self.var_date.get(),
            "out": self.var_out.get(),
            "verified": self.var_verified.get(),
            "skip": self.var_skip.get(),
            "dry": self.var_dry.get(),
        }
        self._clear_log()
        self.stop_event.clear()
        self._set_running(True)
        self.var_report.set("")
        threading.Thread(target=self._worker, args=(params,), daemon=True).start()

    def _on_stop(self):
        self.stop_event.set()
        self.var_status.set("正在停止…（会连带中止 harvest 子进程）")

    def _on_closing(self):
        if self.running:
            if not messagebox.askyesno("正在跑", "管线还在运行，确定停止并退出？"):
                return
            self.stop_event.set()
            self.after(600, self.destroy)
            return
        self.destroy()

    # ---- 运行

    def _worker(self, params):
        date_label = (params.get("date") or "").strip() or \
            datetime.now().strftime("%Y-%m-%d")
        ns = build_namespace(params.get("root"), params.get("date"),
                             params.get("out"), params.get("verified"),
                             params.get("skip"), params.get("dry"))
        sink = _LogSink(q=self.q)
        old = sys.stdout
        rc = 1
        try:
            sys.stdout = sink
            mod = load_increment_module(params["root"])
            mod.subprocess = _ProcShim(sink.emit, self.stop_event.is_set)
            rc = mod.run_increment(ns)
        except Exception:
            sink.emit("\n[!] GUI 运行异常：\n%s\n" % traceback.format_exc())
            rc = 1
        finally:
            sys.stdout = old
            self.q.put(("done", (rc, params["root"], ns.out, date_label)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self._append(payload)
                elif kind == "status":
                    self.var_status.set(payload)
                elif kind == "state":
                    self._set_running(payload == "run")
                elif kind == "done":
                    self._on_done(*payload)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append(self, line):
        if line.startswith("[!]"):
            tag = "err"
        elif line.startswith("[i]") or line.startswith("下一步"):
            tag = "info"
        elif line.startswith("    "):
            tag = "dim"
        else:
            tag = None
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_running(self, run):
        self.running = run
        self.btn_start.configure(state="disabled" if run else "normal")
        self.btn_stop.configure(state="normal" if run else "disabled")
        if run:
            self.var_status.set("运行中…")

    def _on_done(self, rc, root, out, date_label):
        self._set_running(False)
        if self.stop_event.is_set() and rc not in (0, 1, 2):
            rc = 0
        self.var_status.set(RC_TEXT.get(rc, "退出码 %s" % rc))
        report = os.path.join(root, out, "increment_report_%s.md" % date_label)
        if os.path.isfile(report):
            self.var_report.set(report)
            self.btn_report.configure(state="normal")
        self.btn_dir.configure(state="normal")
        self._append("")
        self._append("[i] 结束，退出码 %d：%s" % (rc, RC_TEXT.get(rc, "?")))

    # ---- 打开

    def _open_report(self):
        p = self.var_report.get()
        if p and os.path.isfile(p):
            self._open_path(p)

    def _open_dir(self):
        root = self.var_root.get().strip()
        out = self.var_out.get().strip() or DEFAULT_OUT
        d = out if os.path.isabs(out) else os.path.join(root, out)
        if os.path.isdir(d):
            self._open_path(d)
        else:
            messagebox.showinfo("打开台账目录", "目录不存在（还没跑过？）：\n%s" % d)

    @staticmethod
    def _open_path(p):
        try:
            os.startfile(p)  # noqa: S606  # Windows 专用
        except Exception as e:
            messagebox.showinfo("打开失败", "请手动打开：\n%s\n\n%s" % (p, e))


# ---------------------------------------------------------------- 入口

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return run_selftest([a for a in argv if a != "--selftest"])
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    IncrementApp().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
