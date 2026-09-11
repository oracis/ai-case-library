# -*- coding: utf-8 -*-
"""build_static.py 的测试。

重点盯「防误删」护栏 —— 构建脚本会 rmtree 输出目录，这段逻辑一旦出问题
就是删用户文件，所以必须有测试兜着。另外验证构建自检真的能拦住坏产物
（比如哪天有人把相对路径改回绝对路径）。

不联网。用法：python scripts/test_build_static.py
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "build_static", os.path.join(ROOT, "scripts", "build_static.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def quiet(fn, *a, **kw):
    """跑一个会狂打印的函数，把输出吞掉，返回 (rc, 输出)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


def parse_data_js(path):
    """从 data.js 里抠出那段 JSON 并解析（不依赖 node）。"""
    js = open(path, encoding="utf-8").read()
    marker = "window.__CASE_LIB_DATA__ = "
    i = js.index(marker) + len(marker)
    body = js[i:]
    body = body[:body.rindex(";")]
    return json.loads(body)


def main():
    mod = load_mod()
    orig_static_dir = mod.STATIC_DIR
    orig_anchor = mod.ANCHOR
    tmp_root = tempfile.mkdtemp(prefix="bs_test_")
    print("临时目录：%s\n" % tmp_root)

    try:
        # ---------------- 1 防误删护栏 ----------------
        print("[1] prepare_out 的防误删护栏")
        chk("拒绝项目根目录", mod.prepare_out(ROOT) is None)

        for guard in ("static", "data", "scripts"):
            chk("拒绝源码目录 %s/" % guard,
                mod.prepare_out(os.path.join(ROOT, guard)) is None)

        with_git = os.path.join(tmp_root, "looks_like_repo")
        os.makedirs(os.path.join(with_git, ".git"), exist_ok=True)
        chk("拒绝含 .git 的目录", mod.prepare_out(with_git) is None)
        chk("  且没动那个 .git", os.path.isdir(os.path.join(with_git, ".git")))

        normal = os.path.join(tmp_root, "normal")
        os.makedirs(normal, exist_ok=True)
        with open(os.path.join(normal, "old_leftover.txt"), "w", encoding="utf-8") as f:
            f.write("上个版本的残留")
        out = mod.prepare_out(normal)
        chk("正常目录返回路径", out is not None and os.path.isdir(out))
        chk("  清掉了残留", not os.path.exists(os.path.join(normal, "old_leftover.txt")))

        # ---------------- 2 正常构建 ----------------
        print("\n[2] 正常构建")
        site = os.path.join(tmp_root, "site")
        rc, log = quiet(mod.build, site)
        chk("返回 0", rc == 0, "rc=%s" % rc)
        if rc != 0:
            print("      构建输出：\n%s" % log)
            return 1

        for n in ("index.html", "style.css", "app.js", "data.js", "data.json", "404.html"):
            chk("产出 %s" % n, os.path.isfile(os.path.join(site, n)))

        html = open(os.path.join(site, "index.html"), encoding="utf-8").read()
        chk("注入了静态标记", "window.__STATIC__ = true" in html)
        chk("  标记在 app.js 之前", html.index("__STATIC__") < html.index("./app.js"))
        chk("style.css 走相对路径", "./style.css" in html)
        chk("app.js 走相对路径", "./app.js" in html)
        chk("页面引用 data.js", "data.js" in html)
        chk("没有根路径引用（子目录部署才不会 404）",
            'href="/' not in html and 'src="/' not in html)

        # ---------------- 3 数据一致性 ----------------
        print("\n[3] 产物里的数据")
        data = parse_data_js(os.path.join(site, "data.js"))
        src = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
        chk("data.js 能解析出 __CASE_LIB_DATA__", isinstance(data, dict))
        chk("案例数与 data/cases.json 一致",
            len(data.get("cases") or []) == len(src),
            "%d vs %d" % (len(data.get("cases") or []), len(src)))
        for k in ("candidates", "inbox", "sources", "stats", "generated_at"):
            chk("含字段 %s" % k, k in data)
        j = json.load(open(os.path.join(site, "data.json"), encoding="utf-8"))
        chk("data.json 与 data.js 内容一致", j == data)

        js_body = open(os.path.join(site, "data.js"), encoding="utf-8").read()
        chk("data.js 里没有裸露的 </script>", "</script" not in js_body)

        # ---------------- 4 --no-inbox ----------------
        print("\n[4] --no-inbox 精简版")
        lite = os.path.join(tmp_root, "lite")
        rc, _ = quiet(mod.build, lite, include_inbox=False)
        chk("返回 0", rc == 0)
        d2 = parse_data_js(os.path.join(lite, "data.js"))
        chk("采集队列被清空", d2.get("inbox") == [])
        chk("但案例仍在", len(d2.get("cases") or []) == len(src))
        chk("stats 里标注了未包含", (d2.get("stats") or {}).get("inbox_included") is False)

        # ---------------- 5 注入锚点缺失要报错，不能静默出个坏站 ----------------
        print("\n[5] 注入锚点缺失时应当失败")
        mod.ANCHOR = '<script src="./does-not-exist.js"></script>'
        rc, log = quiet(mod.build, os.path.join(tmp_root, "fail_anchor"))
        chk("返回 1（失败）", rc == 1, "rc=%s" % rc)
        chk("  输出里有定位信息", "找不到" in log)
        mod.ANCHOR = orig_anchor

        # ---------------- 6 绝对路径要被构建自检拦下 ----------------
        print("\n[6] index.html 里退回绝对路径时应当失败")
        fake = os.path.join(tmp_root, "fake_static")
        os.makedirs(fake, exist_ok=True)
        for n in ("style.css", "app.js"):
            shutil.copy2(os.path.join(orig_static_dir, n), os.path.join(fake, n))
        broken = open(os.path.join(orig_static_dir, "index.html"), encoding="utf-8").read()
        broken = broken.replace('href="./style.css"', 'href="/style.css"')
        with open(os.path.join(fake, "index.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(broken)

        mod.STATIC_DIR = fake
        rc, log = quiet(mod.build, os.path.join(tmp_root, "fail_root_path"))
        chk("返回 1（失败）", rc == 1, "rc=%s" % rc)
        chk("  报出的是根路径问题", "根路径引用" in log)
        mod.STATIC_DIR = orig_static_dir

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print()
    print("=" * 56)
    print("  结果：%d 通过 / %d 失败" % (passed, len(fails)))
    if fails:
        for f in fails:
            print("    ✗ %s" % f)
    print("=" * 56)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
