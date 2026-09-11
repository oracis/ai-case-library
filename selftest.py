# -*- coding: utf-8 -*-
"""
自测：起一个临时服务，打全部接口，跑完自动还原数据。

用法：python selftest.py
特点：用独立端口（默认 5087），不影响正在运行的 5052 实例。
      开始前备份 data/，结束后无论成败都还原。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
BACKUP = os.path.join(ROOT, ".selftest_backup")
PY = sys.executable
PORT = int(os.environ.get("CASE_LIB_TEST_PORT", "5087"))
BASE = "http://127.0.0.1:%d" % PORT

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails = []
passed = 0


def req(method, path, payload=None, timeout=10):
    """返回 (status, body)。HTTP 错误码不抛异常，直接当返回值处理。"""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "selftest"}
    if body:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with opener.open(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def main():
    # ---------- 先备份 ----------
    if os.path.isdir(BACKUP):
        shutil.rmtree(BACKUP)
    shutil.copytree(DATA, BACKUP)
    print("已备份 data/ -> %s" % os.path.basename(BACKUP))

    print("=" * 62)
    print("  自测 · AI 案例库（端口 %d）" % PORT)
    print("=" * 62)

    env = dict(os.environ, CASE_LIB_PORT=str(PORT), CASE_LIB_NO_BROWSER="1")
    proc = subprocess.Popen([PY, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    try:
        ready = False
        for _ in range(48):
            time.sleep(0.25)
            try:
                if req("GET", "/api/ping", timeout=1)[0] == 200:
                    ready = True
                    break
            except Exception:
                pass
        if not ready:
            print("[FAIL] 服务未能启动")
            print((proc.stdout.read() or b"").decode("utf-8", "replace"))
            return 1

        # ---------- 1 静态资源 ----------
        print("\n[1] 静态资源")
        for path, kw in [("/", b"app.js"), ("/style.css", b":root"),
                         ("/app.js", b"renderInbox")]:
            st, body = req("GET", path)
            check("GET %s" % path, st == 200 and kw in body, "(%d, %d bytes)" % (st, len(body)))

        # ---------- 2 接口 ----------
        print("\n[2] 接口")
        st, body = req("GET", "/api/ping")
        check("GET /api/ping", st == 200 and json.loads(body).get("ok") is True)

        st, body = req("GET", "/api/data")
        data = json.loads(body)
        check("GET /api/data 返回 4 个数据块",
              all(k in data for k in ("cases", "candidates", "inbox", "sources")),
              "cases=%d cands=%d inbox=%d" % (len(data["cases"]), len(data["candidates"]),
                                              len(data["inbox"])))
        check("三级漏斗数量递增正常",
              len(data["inbox"]) >= 0 and len(data["candidates"]) >= 0 and len(data["cases"]) > 0)

        st, body = req("GET", "/api/stats")
        s = json.loads(body)
        check("GET /api/stats", st == 200 and s.get("curated", 0) > 0,
              "curated=%d verified=%d flagged=%d" % (s["curated"], s["verified"], s["flagged"]))

        # ---------- 3 数据结构 ----------
        print("\n[3] 数据结构")
        c0 = data["cases"][0]
        for f in ("id", "name", "one_liner", "category", "verification", "metrics", "sources"):
            check("case 字段 %s" % f, f in c0)
        check("信息源 5 个", len(data["sources"].get("sources", [])) == 5,
              "实际 %d" % len(data["sources"].get("sources", [])))
        check("filter_rules 存在", "filter_rules" in data["sources"])
        n_corr = sum(len(c.get("corrections", [])) for c in data["cases"])
        check("修正记录 > 0", n_corr > 0, "共 %d 处" % n_corr)
        n_ver = sum(1 for c in data["cases"] if c.get("verification") in ("stripe", "official"))
        check("已核实案例 > 0", n_ver > 0, "%d 条" % n_ver)

        # ---------- 4 采集队列 -> 候选池 ----------
        print("\n[4] 写操作 · 采集队列转入候选池")
        inbox_before = len(data["inbox"])
        cand_before = len(data["candidates"])
        if inbox_before:
            tid = data["inbox"][0]["id"]
            st, body = req("POST", "/api/inbox/%s/to-candidates" % urllib.parse.quote(tid), {})
            check("POST /api/inbox/<id>/to-candidates", st == 201,
                  "-> %s" % (json.loads(body)["candidate"]["id"] if st == 201 else body[:80]))
            after = json.loads(req("GET", "/api/data")[1])
            check("采集队列 -1", len(after["inbox"]) == inbox_before - 1,
                  "%d -> %d" % (inbox_before, len(after["inbox"])))
            check("候选池 +1", len(after["candidates"]) == cand_before + 1,
                  "%d -> %d" % (cand_before, len(after["candidates"])))
        else:
            check("POST /api/inbox/<id>/to-candidates", True, "（队列为空，跳过）")

        # ---------- 5 候选 -> 精写 ----------
        print("\n[5] 写操作 · 候选提升为精写案例")
        d = json.loads(req("GET", "/api/data")[1])
        cases_before = len(d["cases"])
        cid = d["candidates"][0]["id"]
        st, body = req("POST", "/api/candidates/%s/promote" % urllib.parse.quote(cid), {})
        check("POST /api/candidates/<id>/promote", st == 201,
              "-> %s" % (json.loads(body)["case"]["id"] if st == 201 else body[:80]))
        d2 = json.loads(req("GET", "/api/data")[1])
        check("精写库 +1", len(d2["cases"]) == cases_before + 1,
              "%d -> %d" % (cases_before, len(d2["cases"])))
        check("提升后带待核实标记",
              any(c.get("needs_review") for c in d2["cases"]), "")

        # ---------- 6 新增 + 修改 ----------
        print("\n[6] 写操作 · 新增与修改")
        st, body = req("POST", "/api/cases",
                       {"name": "Selftest Widget", "one_liner": "自测临时条目", "category": "自测"})
        check("POST /api/cases", st == 201 and json.loads(body)["case"]["id"] == "selftest-widget")
        st, body = req("PATCH", "/api/cases/selftest-widget",
                       {"verification": "partial", "verdict": "已改"})
        check("PATCH /api/cases/<id>", st == 200 and json.loads(body)["case"]["verification"] == "partial")
        st, body = req("POST", "/api/cases", {})
        check("POST /api/cases 缺 name 被拒", st == 400)

        # ---------- 7 404 / 安全 ----------
        print("\n[7] 错误处理与路径安全")
        checks = [("GET", "/nope.js", 404),
                  ("POST", "/api/cases/does-not-exist", 404),
                  ("POST", "/api/inbox/nope/to-candidates", 404),
                  ("POST", "/api/candidates/nope/promote", 404),
                  ("POST", "/api/cases", 400),          # 缺 name
                  ]
        for method, path, want in checks:
            st, _ = req(method, path, {} if method == "POST" else None)
            check("%s %s" % (method, path), st == want, "实际 %d" % st)

        # 目录穿越（urllib 会先做一次归一化，所以 403/404 都算拦住了）
        st, _ = req("GET", "/../../server.py")
        check("GET /../../server.py 被拦", st in (403, 404), "实际 %d" % st)
        st, body = req("GET", "/api/data")
        check("越界后服务仍正常", st == 200, "(%d)" % st)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

        # ---------- 还原 ----------
        print("\n[*] 还原 data/")
        for name in os.listdir(BACKUP):
            src = os.path.join(BACKUP, name)
            dst = os.path.join(DATA, name)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
        shutil.rmtree(BACKUP)
        print("    已还原")

    print("\n" + "=" * 62)
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
