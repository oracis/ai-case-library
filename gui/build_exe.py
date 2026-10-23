# -*- coding: utf-8 -*-
"""把 gui/htm_increment_gui.py 打成单文件 exe（PyInstaller，onefile + 无控制台）。

用法：
    python -X utf8 gui/build_exe.py

产出：
    gui/dist/htm-increment-gui.exe

说明：
  · exe 自身不含管线逻辑——运行时用 importlib 加载项目里的
    scripts/htm_increment.py，所以项目代码改了不用重新打包；
    但换机器/换项目目录要在窗口里「浏览…」指一下根目录。
  · 打包用的 python 必须有 tkinter + PyInstaller。本机 C:\\Python315 两个都有；
    PATH 上的 python（Store 3.13）没有 tkinter，不能用来打包。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(HERE, "htm_increment_gui.py")
ICON = os.path.join(HERE, "htm-increment-gui.ico")

# 候选解释器：先用当前 python，再试本机常见的几个
CANDIDATES = [
    sys.executable,
    r"C:\Python315\python.exe",
    r"C:\Python313\python.exe",
]


def pick_python():
    tried = []
    for py in CANDIDATES:
        if not py or not os.path.isfile(py) or py in tried:
            continue
        tried.append(py)
        try:
            r = subprocess.run([py, "-c", "import tkinter, PyInstaller"],
                               capture_output=True, timeout=60)
        except Exception:
            continue
        if r.returncode == 0:
            return py
    return None


def main():
    if not os.path.isfile(APP):
        print("[!] 找不到 %s" % APP)
        return 1
    if not os.path.isfile(ICON):
        # 图标是入库资产（gui/htm-increment-gui.ico），不在就现生成：
        # ① zero-dep-icon-gen skill 出各尺寸 PNG
        #    PY=C:/Users/DELL/.workbuddy/binaries/python/versions/3.13.12/python.exe
        #    S=$HOME/.workbuddy/skills/zero-dep-icon-gen/scripts/gen_icons.py
        #    for SZ in 16 32 48 64 256; do $PY -X utf8 "$S" \
        #      --spec "htm-increment-gui-$SZ:#4F46E5,#818CF8,search" --size $SZ -o tmp/icon_png; done
        # ② gui/png2ico.py 打成 BMP 条目 ICO（PNG 条目 .NET Icon 读不稳，别用）
        #    python -X utf8 gui/png2ico.py -o gui/htm-increment-gui.ico --bmp \
        #      tmp/icon_png/htm-increment-gui-*.png
        print("[!] 找不到图标 %s——按上面注释里的两步重新生成" % ICON)
        return 1
    py = pick_python()
    if not py:
        print("[!] 没找到同时带 tkinter 和 PyInstaller 的 python。")
        print("    本机可用：C:\\Python315\\python.exe")
        print("    或给当前 python 装：python -m pip install pyinstaller"
              "（tkinter 装不了，需官方安装器勾选）")
        return 1
    print("[*] 用 %s 打包" % py)

    cmd = [
        py, "-m", "PyInstaller",
        "--onefile", "--noconsole", "--clean",
        "--name", "htm-increment-gui",
        # ⚠ --icon 必须绝对路径：PyInstaller 按 --workpath 解析相对路径，不是 cwd
        "--icon", ICON,
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", HERE,
        APP,
    ]
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("[!] PyInstaller 退出码 %d" % r.returncode)
        return r.returncode

    exe = os.path.join(HERE, "dist", "htm-increment-gui.exe")
    if not os.path.isfile(exe):
        print("[!] 构建结束但没找到 %s" % exe)
        return 1
    print("\n[OK] %s（%.1f MB）" % (exe, os.path.getsize(exe) / 1048576))
    print("     双击即用；建议放在 gui\\dist\\ 里别挪远，"
          "项目目录会自动向上探测，换位置就在窗口里「浏览…」。")
    print("     验证：gui\\dist\\htm-increment-gui.exe --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
