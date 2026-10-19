# -*- coding: utf-8 -*-
"""发布日预检：一条命令回答「现在能不能开干」。

只读，不写任何 data/ 文件，不点任何按钮。

检查项：
  1. CDP 端口 9222（公众号）/ 9223（头条、小红书）是否活着
  2. --deep 时实测公众号登录态（新开 tab 抠 token，用完即关）
  3. 四平台台账计数 + 最近一条
  4. 数据健康（cases 总数、缺评分/内容字段条数、tier 分布）
  5. 覆盖度（cases 里从未出现在某平台台账的条数）

退出码：0 全部 PASS / 仅有 INFO；1 有 FAIL（端口死或登录掉）；2 仅有 WARN。

用法：
    python -X utf8 scripts/preflight.py
    python -X utf8 scripts/preflight.py --deep          # 实测公众号登录态
    python -X utf8 scripts/preflight.py --deep --port 9222
"""
import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
_marks = []
_rows = []


def say(level, name, detail=""):
    _rows.append((level, name, detail))
    print("[%-4s] %-22s %s" % (level, name, detail), flush=True)


def load(name, default):
    p = os.path.join(DATA, name + ".json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return default


def port_alive(port, timeout=2.5):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % port,
                     timeout=timeout) as r:
            return True, json.loads(r.read().decode("utf-8", "replace")).get(
                "Browser", "?")
    except Exception:
        return False, ""


def wechat_login(port):
    """在子进程里实测登录态（eval 没有超时，主进程不能直接被它拖死）。

    子进程先打印 TID <targetId>，父进程超时后按 id 关掉那页，避免留孤儿 tab。
    """
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import time, wechat_publish as wp;"
        "cdp = wp.CDP(%d); tid = cdp.new_target(wp.MP_ENTRY)['id'];"
        "print('TID ' + tid, flush=True); cdp.connect_target(tid);"
        "tok = None\n"
        "for _ in range(10):\n"
        "    time.sleep(1)\n"
        "    try:\n"
        "        tok = wp._get_token(cdp)\n"
        "    except Exception:\n"
        "        tok = None\n"
        "    if tok and tok != 'NO_TOKEN':\n"
        "        break\n"
        "print('RESULT ' + ('ok ' + str(tok) if tok and tok != 'NO_TOKEN' else 'fail'))\n"
        "try:\n"
        "    cdp.close_target(tid)\n"
        "except Exception:\n"
        "    pass\n"
    ) % (os.path.join(ROOT, "scripts"), port)
    import subprocess

    tid = None
    try:
        p = subprocess.Popen([sys.executable, "-u", "-X", "utf8", "-c", code],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, encoding="utf-8", errors="replace")
        t0 = time.time()
        while time.time() - t0 < 40:
            line = p.stdout.readline()
            if not line:
                if p.poll() is not None:
                    break
                continue
            line = line.strip()
            if line.startswith("TID "):
                tid = line[4:].strip()
            elif line.startswith("RESULT "):
                body = line[7:].strip()
                p.wait(timeout=5)
                if body.startswith("ok"):
                    return True, "token=%s" % body[3:].strip()
                return False, "会话过期，需人工扫码"
        p.kill()
        return False, "40 秒内没拿到 token（页面加载慢或需扫码）"
    except Exception as e:
        return False, "子进程异常: %s" % e
    finally:
        if tid:
            try:  # 超时被杀时子进程来不及关页，这里补关
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                op.open("http://127.0.0.1:%d/json/close/%s" % (port, tid),
                        timeout=3).read()
            except Exception:
                pass


def ledger_ids(d):
    if isinstance(d, dict):
        return set(d.keys())
    if isinstance(d, list):
        return {str(x.get("id")) for x in d if isinstance(x, dict) and x.get("id")}
    return set()


def main():
    ap = argparse.ArgumentParser(description="发布日预检（只读）")
    ap.add_argument("--deep", action="store_true",
                    help="实测公众号登录态（会新开一个 tab，约 5-20 秒）")
    ap.add_argument("--port", type=int, default=9222,
                    help="公众号 CDP 端口（默认 9222）")
    args = ap.parse_args()

    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)

    print("=" * 66)
    print("  发布日预检  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 66)

    # 1. CDP 端口
    hints = {args.port: "scripts/_launch_9222.py", 9223: "scripts/_launch_9223.py"}
    for port, label in ((args.port, "公众号 CDP"), (9223, "头条/小红书 CDP")):
        ok, browser = port_alive(port)
        if ok:
            say(PASS, "%s :%d" % (label, port), browser)
        else:
            say(FAIL, "%s :%d" % (label, port),
                "未监听 → python -X utf8 %s" % hints.get(port, "启动器"))

    # 2. 公众号登录态
    if args.deep:
        if not port_alive(args.port)[0]:
            say(WARN, "公众号登录态", "跳过（端口未开）")
        else:
            ok, detail = wechat_login(args.port)
            say(PASS if ok else FAIL, "公众号登录态", detail)
    else:
        say(INFO, "公众号登录态", "未测（加 --deep 实测，需扫码时会有明确提示）")

    # 3. 台账
    wx = load("wechat_published", {})
    tt = load("toutiao_published", [])
    bl = load("bili_published", [])
    xh = load("xhs_published", [])
    for name, d in (("公众号", wx), ("头条", tt), ("B站", bl), ("小红书", xh)):
        ids = ledger_ids(d)
        last = ""
        if isinstance(d, dict) and d:
            k = list(d.keys())[-1]
            last = str((d[k] or {}).get("published_at", "") or "")[:16]
        elif isinstance(d, list) and d:
            last = str(d[-1].get("at", ""))[:16]
        say(PASS if ids else WARN, "台账 %s" % name,
            "%d 条%s" % (len(ids), ("  最近 " + last) if last else ""))

    # 4. 数据健康
    cases = load("cases", [])
    if not isinstance(cases, list) or not cases:
        say(FAIL, "cases.json", "读不到或为空")
    else:
        miss = {
            "china_fit": sum(1 for c in cases if not c.get("china_fit")),
            "solo_fit": sum(1 for c in cases if not c.get("solo_fit")),
            "why_it_works": sum(1 for c in cases if not c.get("why_it_works")),
            "playbook": sum(1 for c in cases if not c.get("playbook")),
        }
        bad = {k: v for k, v in miss.items() if v}
        say(PASS if not bad else WARN, "cases 数据完整度",
            "%d 条" % len(cases) + ("；缺 " + ", ".join(
                "%s×%d" % (k, v) for k, v in bad.items()) if bad else "；四项齐备"))

        tiers = {}
        for c in cases:
            tiers[c.get("tier") or "?"] = tiers.get(c.get("tier") or "?", 0) + 1
        say(INFO, "tier 分布", " / ".join(
            "%s %d" % (k, tiers[k]) for k in sorted(tiers)))

    # 5. 覆盖度
    if isinstance(cases, list) and cases:
        all_ids = {str(c.get("id")) for c in cases if c.get("id")}
        cover = [("公众号", ledger_ids(wx)), ("头条", ledger_ids(tt)),
                 ("B站", ledger_ids(bl)), ("小红书", ledger_ids(xh))]
        parts = []
        for name, ids in cover:
            n = len(ids & all_ids)
            parts.append("%s %d/%d" % (name, n, len(all_ids)))
        say(INFO, "案例覆盖", "  ".join(parts))

    # 汇总
    n_fail = sum(1 for lv, _, _ in _rows if lv == FAIL)
    n_warn = sum(1 for lv, _, _ in _rows if lv == WARN)
    print("-" * 66)
    if n_fail:
        print("结论：%d 项 FAIL，先修再发。" % n_fail)
        return 1
    if n_warn:
        print("结论：可以发，但有 %d 项 WARN 需留意。" % n_warn)
        return 2
    print("结论：全绿，可以开干。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
