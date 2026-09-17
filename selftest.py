# -*- coding: utf-8 -*-
"""
自测：起一个临时服务，打全部接口，跑完自动还原数据。

用法：python selftest.py
特点：用独立端口（默认 5087 公开 / 5088 后台），不影响正在运行的 5052 实例。
      开始前备份 data/，结束后无论成败都还原。

server.py 现在是「一个进程、两个站点」：公开站只读，后台站才能写。
所以这里有两套请求函数：
  req()   —— 打公开站，用来测「读者看到的东西」和「公开站不该有写接口」
  areq()  —— 打后台站，用来测核实、入库这些写操作
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
ADMIN_PORT = PORT + 1                       # server.py 默认后台 = 公开 +1
BASE = "http://127.0.0.1:%d" % PORT
ADMIN_BASE = "http://127.0.0.1:%d" % ADMIN_PORT

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails = []
passed = 0


def _go(base, method, path, payload, timeout):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "selftest"}
    if body:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with opener.open(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def req(method, path, payload=None, timeout=10):
    """打公开站。返回 (status, body)。HTTP 错误码不抛异常，直接当返回值处理。"""
    return _go(BASE, method, path, payload, timeout)


def areq(method, path, payload=None, timeout=10):
    """打后台站。写接口（核实 / 入库）只在这个端口上存在。"""
    return _go(ADMIN_BASE, method, path, payload, timeout)


def req_at(base, method, path, payload=None, cookie=None, timeout=10):
    """跟 req 一样，但能换端口、能带 Cookie，并返回响应头（要读 Set-Cookie）。"""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "selftest"}
    if body:
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    r = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with opener.open(r, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def run_auth_section():
    """起一台带鉴权的服务，验证「核实只对管理员开放」这件事真的成立。

    注意它会写 data/admin.json（凭据文件），而 main() 的通用还原只拷回
    备份里已有的文件、不会删新建的，所以这里必须自己收尾：
    原本没有就删掉，原本有就原样还回去。否则一次自测会把用户的密码换掉。
    """
    print("\n[8] 后台鉴权（核实只对管理员开放）")
    # 另起一台：公开 PORT+10 / 后台 PORT+11，避免和主测试的 5087/5088 撞车。
    # 鉴权断言全部打后台端口 —— 公开站上这些接口根本不存在。
    apub = PORT + 10
    aadm = PORT + 11
    apub_base = "http://127.0.0.1:%d" % apub
    abase = "http://127.0.0.1:%d" % aadm
    apw = "selftest-pw-%d" % int(time.time())

    admin_file = os.path.join(DATA, "admin.json")
    had_admin = os.path.exists(admin_file)
    saved = None
    if had_admin:
        with open(admin_file, "rb") as f:
            saved = f.read()

    env = dict(os.environ, CASE_LIB_PORT=str(apub), CASE_LIB_ADMIN_PORT=str(aadm),
               CASE_LIB_NO_BROWSER="1", CASE_LIB_ADMIN_PASSWORD=apw)
    proc = subprocess.Popen([PY, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(48):
            time.sleep(0.25)
            try:
                if req_at(abase, "GET", "/api/ping", timeout=1)[0] == 200:
                    ready = True
                    break
            except Exception:
                pass
        if not ready:
            out = ""
            try:
                proc.kill()
                out = (proc.stdout.read() or b"").decode("utf-8", "replace")
            except Exception:
                pass
            check("带鉴权的服务能启动", False, out[:200])
            return

        # --- 同一台服务的公开站：连门都没有 ---
        # 这是「分成两个站」真正的意义：不是「有门但要口令」，
        # 而是请求过来压根没有这个路由，扫站扫不出这儿有个后台。
        for method, path in [("POST", "/api/login"),
                             ("POST", "/api/logout"),
                             ("GET", "/api/verify-schema"),
                             ("PUT", "/api/candidates/x/verification"),
                             ("POST", "/api/candidates/x/promote"),
                             ("POST", "/api/cases")]:
            st, _, _ = req_at(apub_base, method, path, {} if method != "GET" else None)
            check("公开站 %s %s -> 404" % (method, path), st == 404, "实际 %d" % st)
        st, body, _ = req_at(apub_base, "GET", "/verify.js")
        check("公开站拿不到后台脚本", st == 404, "实际 %d" % st)
        st, body, _ = req_at(apub_base, "GET", "/")
        check("公开站首页没有后台入口", st == 200 and b"admin-btn" not in body)
        st, body, _ = req_at(abase, "GET", "/")
        check("后台首页有登录框", st == 200 and b"login-pw" in body)

        # --- 游客（后台端口） ---
        st, body, _ = req_at(abase, "GET", "/api/session")
        check("游客 session.admin = false",
              st == 200 and json.loads(body).get("admin") is False)

        # 鉴权挡在所有路由之前，所以这里用不存在的 id 也应该拿到 401 而不是 404。
        # 反过来说：要是哪天返回了 404/200，就说明闸门被挪到后面去了。
        for method, path in [("GET", "/api/verify-schema"),
                             ("PUT", "/api/candidates/eloquent/verification"),
                             ("POST", "/api/candidates/eloquent/promote"),
                             ("PATCH", "/api/cases/sierra/tier"),
                             ("POST", "/api/inbox/x/to-candidates")]:
            st, _, _ = req_at(abase, method, path, {} if method != "GET" else None)
            check("游客 %s %s -> 401" % (method, path.split("/api")[1][:28]), st == 401,
                  "实际 %d" % st)

        st, body, _ = req_at(abase, "GET", "/api/data")
        check("游客拿不到核实草稿",
              st == 200 and json.loads(body).get("verifications") == {})
        st, body, _ = req_at(abase, "GET", "/api/data")
        check("游客仍可正常浏览案例",
              st == 200 and len(json.loads(body).get("cases", [])) > 0)
        # 用一条真实存在的候选来测写接口，别写死 id —— 前面几节会把它提走
        some_cand = (json.loads(body).get("candidates") or [{}])[0].get("id", "eloquent")

        # --- 登录 ---
        st, _, _ = req_at(abase, "POST", "/api/login", {"password": "definitely-wrong"})
        check("错误密码登录 -> 401", st == 401, "实际 %d" % st)

        st, body, heads = req_at(abase, "POST", "/api/login", {"password": apw})
        check("正确密码登录 -> 200", st == 200, "实际 %d" % st)
        set_cookie = heads.get("Set-Cookie", "")
        check("下发 HttpOnly Cookie", "HttpOnly" in set_cookie and "case_admin=" in set_cookie,
              set_cookie[:60])
        cookie = ""
        for part in set_cookie.split(";"):
            if part.strip().startswith("case_admin="):
                cookie = part.strip()
        check("取到登录凭据", bool(cookie))

        st, body, _ = req_at(abase, "GET", "/api/session", cookie=cookie)
        check("带 cookie 后 admin = true",
              st == 200 and json.loads(body).get("admin") is True)
        st, _, _ = req_at(abase, "GET", "/api/verify-schema", cookie=cookie)
        check("登录后可读规则表", st == 200, "实际 %d" % st)
        st, _, _ = req_at(abase, "PUT",
                          "/api/candidates/%s/verification" % urllib.parse.quote(some_cand),
                          {"gates": []}, cookie=cookie)
        check("登录后写接口放行", st == 200, "实际 %d" % st)

        # 伪造 / 篡改的凭据必须无效
        st, _, _ = req_at(abase, "GET", "/api/verify-schema",
                          cookie="case_admin=1234567890.deadbeef")
        check("伪造 cookie -> 401", st == 401, "实际 %d" % st)

        # --- 登出 ---
        st, _, _ = req_at(abase, "POST", "/api/logout", cookie=cookie)
        check("登出 -> 200", st == 200, "实际 %d" % st)
        st, _, _ = req_at(abase, "GET", "/api/verify-schema", cookie=cookie)
        check("登出后旧凭据立即失效", st == 401, "实际 %d" % st)

        # --- 登录限流 ---
        # 公开仓库意味着攻击者知道密码怎么比对、token 怎么签，唯一挡着他的
        # 就是「试不快」和「试不多」。这条守住后半句。
        codes = []
        for i in range(6):
            st, _, _ = req_at(abase, "POST", "/api/login", {"password": "nope-%d" % i})
            codes.append(st)
        check("前 5 次错密码是 401", codes[:5] == [401] * 5, str(codes[:5]))
        check("第 6 次起被锁为 429", codes[5] == 429, str(codes[5]))
        st, _, _ = req_at(abase, "POST", "/api/login", {"password": apw})
        check("锁定期间正确密码也进不来（429）", st == 429, "实际 %d" % st)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        # 凭据文件必须复原，否则一次自测就把用户的管理员密码换掉了
        try:
            if had_admin and saved is not None:
                with open(admin_file, "wb") as f:
                    f.write(saved)
            elif not had_admin and os.path.exists(admin_file):
                os.remove(admin_file)
        except Exception as e:                                # noqa: BLE001
            print("  [警告] 未能复原 data/admin.json：%s" % e)


def main():
    # ---------- 先备份 ----------
    if os.path.isdir(BACKUP):
        shutil.rmtree(BACKUP)
    shutil.copytree(DATA, BACKUP)
    print("已备份 data/ -> %s" % os.path.basename(BACKUP))

    print("=" * 62)
    print("  自测 · AI 案例库（端口 %d）" % PORT)
    print("=" * 62)

    # 关掉鉴权：本文件的断言写于「写接口可直接调用」的年代，
    # 让它们继续验证业务逻辑本身；鉴权另开一节单独测（见 [6]）。
    env = dict(os.environ, CASE_LIB_PORT=str(PORT), CASE_LIB_NO_BROWSER="1",
               CASE_LIB_NO_AUTH="1")
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
            check("GET 公开站 %s" % path, st == 200 and kw in body,
                  "(%d, %d bytes)" % (st, len(body)))
        # 后台是另一套文件，另一个端口
        for path, kw in [("/", b"login-pw"), ("/app.js", b"loadAdmin"),
                         ("/verify.js", b"openVerify"), ("/style.css", b".adm-")]:
            st, body = areq("GET", path)
            check("GET 后台站 %s" % path, st == 200 and kw in body,
                  "(%d, %d bytes)" % (st, len(body)))

        # ---------- 2 接口 ----------
        print("\n[2] 接口")
        st, body = req("GET", "/api/ping")
        check("GET /api/ping", st == 200 and json.loads(body).get("ok") is True)
        check("ping 认得自己是公开站", json.loads(body).get("site") == "public",
              "实际 %s" % json.loads(body).get("site"))
        st, body = areq("GET", "/api/ping")
        check("后台 ping 认得自己是后台", json.loads(body).get("site") == "admin",
              "实际 %s" % json.loads(body).get("site"))

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
            st, body = areq("POST", "/api/inbox/%s/to-candidates" % urllib.parse.quote(tid), {})
            check("POST /api/inbox/<id>/to-candidates", st == 201,
                  "-> %s" % (json.loads(body)["candidate"]["id"] if st == 201 else body[:80]))
            after = json.loads(areq("GET", "/api/data")[1])
            check("采集队列 -1", len(after["inbox"]) == inbox_before - 1,
                  "%d -> %d" % (inbox_before, len(after["inbox"])))
            check("候选池 +1", len(after["candidates"]) == cand_before + 1,
                  "%d -> %d" % (cand_before, len(after["candidates"])))
        else:
            check("POST /api/inbox/<id>/to-candidates", True, "（队列为空，跳过）")

        # ---------- 5 候选 -> 精写 ----------
        # 核实闸门是刻意加上的：条件不齐不能入库。所以这里要验两件事——
        # ① 什么都不填时被 409 拦住；② 填齐后能发布并判出档次。
        print("\n[5] 写操作 · 核实闸门与发布")
        d = json.loads(areq("GET", "/api/data")[1])
        cases_before = len(d["cases"])
        cands_before = len(d["candidates"])
        cid = d["candidates"][0]["id"]
        p = "/api/candidates/%s/promote" % urllib.parse.quote(cid)

        st, body = areq("POST", p, {})
        check("条件不齐时 promote 被拦（409）", st == 409,
              "实际 %d" % st)
        # 空配置连门槛都没过，所以报的是「门槛未过」而不是「还差必填」；
        # 两者都属于「不能发布」，断言放在这个层面。
        err = json.loads(body).get("error", "") if st == 409 else ""
        check("拦截时说明原因",
              ("门槛未过" in err) or ("必填" in err),
              err[:56])

        d1 = json.loads(areq("GET", "/api/data")[1])
        check("被拦后精写库不变", len(d1["cases"]) == cases_before,
              "%d -> %d" % (cases_before, len(d1["cases"])))

        # 规则表要能被前端取到，界面才不用把规则抄一遍
        st, body = areq("GET", "/api/verify-schema")
        sch = json.loads(body)
        check("GET /api/verify-schema", st == 200 and len(sch["gates"]) == 3 and len(sch["musts"]) == 3,
              "gates=%d musts=%d bonus=%d" % (len(sch.get("gates", [])), len(sch.get("musts", [])),
                                              len(sch.get("bonus", []))))

        # 存草稿（可中断）
        draft = {
            "gates": [g["key"] for g in sch["gates"]],
            "musts": [m["key"] for m in sch["musts"]],
            "bonus": [b["key"] for b in sch["bonus"]],
            "caliber": sch["calibers"][0]["key"],
            "verification": "official",
            "source_kinds": ["official"],
            "sources": [{"label": "自测来源", "url": "https://example.com", "kind": "official"}],
            "note": "自测草稿",
        }
        st, body = areq("PUT", "/api/candidates/%s/verification" % urllib.parse.quote(cid), draft)
        check("PUT 核实草稿", st == 200 and json.loads(body)["result"]["publishable"],
              "-> %s" % json.loads(body).get("result", {}).get("verdict", body[:60]))

        st, body = areq("GET", "/api/candidates/%s/verification" % urllib.parse.quote(cid))
        check("GET 核实草稿可回填", st == 200 and json.loads(body)["draft"].get("caliber") == "arr")

        # 质量分满分 → 应该是精品档
        st, body = areq("POST", p, {"verification_config": draft})
        j = json.loads(body) if st == 201 else {}
        check("条件齐全后可以发布", st == 201, "实际 %d %s" % (st, body[:80] if st != 201 else ""))
        if st == 201:
            check("发布后进精品池（质量分满分）",
                  j["case"].get("tier") == "premium",
                  "tier=%s score=%s" % (j["case"].get("tier"), j["case"].get("quality_score")))
            check("发布后不再带待核实标记", not j["case"].get("needs_review"))
            check("发布后写入核实等级与口径",
                  j["case"].get("verification") == "official" and j["case"].get("caliber") == "arr")

        d2 = json.loads(areq("GET", "/api/data")[1])
        check("精写库 +1", len(d2["cases"]) == cases_before + 1,
              "%d -> %d" % (cases_before, len(d2["cases"])))
        check("候选池 -1", len(d2["candidates"]) == cands_before - 1,
              "%d -> %d" % (cands_before, len(d2["candidates"])))
        check("发布后草稿被清掉", cid not in (d2.get("verifications") or {}))

        # 档位政策（2026-09-17）：来源里有一手证据就默认进精品池，其次才看质量分。
        # 所以「零加分」这一种情况下，档位其实取决于来源 —— 两种都要验。
        if d2["candidates"]:
            # (a) 零加分，但有官方来源 → 按政策进精品池
            cid2 = d2["candidates"][0]["id"]
            light = dict(draft, bonus=[])
            st, body = areq("POST", "/api/candidates/%s/promote" % urllib.parse.quote(cid2),
                            {"verification_config": light})
            j2 = json.loads(body) if st == 201 else {}
            check("零加分但有官方来源 → 进精品池（默认政策）",
                  st == 201 and j2["case"].get("tier") == "premium",
                  "tier=%s" % j2.get("case", {}).get("tier"))
            check("定档理由写进案例（事后可审计）",
                  bool(j2.get("case", {}).get("tier_reason")),
                  (j2.get("case", {}).get("tier_reason") or "")[:46])

        # (b) 零加分且没有一手来源 → 备选池。
        # 备选池必须真的可达，否则这个档位是死的，「先入库等补齐」就无从谈起。
        dmid = json.loads(areq("GET", "/api/data")[1])
        if dmid["candidates"]:
            cidm = dmid["candidates"][0]["id"]
            weak = dict(draft, bonus=[], source_kinds=["review"],
                        sources=[{"label": "评测站", "url": "https://review.example/x",
                                  "kind": "review"}])
            st, body = areq("POST", "/api/candidates/%s/promote" % urllib.parse.quote(cidm),
                            {"verification_config": weak})
            jm = json.loads(body) if st == 201 else {}
            check("无一手来源且零加分 → 进备选池",
                  st == 201 and jm["case"].get("tier") == "backup",
                  "tier=%s" % jm.get("case", {}).get("tier"))

            # 拔档：补上加分项，从备选升精品
            st, body = areq("PATCH", "/api/cases/%s/tier" % urllib.parse.quote(jm["case"]["id"]),
                            {"verification_config": draft})
            check("备选可拔档到精品",
                  st == 200 and json.loads(body)["case"].get("tier") == "premium",
                  "-> %s" % json.loads(body).get("result", {}).get("verdict", "")[:40])

        # 一道都没成立 + 有明确反证时，质量分再高也不能入库。
        # 注意要取一条「还在候选池里」的 —— 上面几条已经被提升走了。
        d3 = json.loads(areq("GET", "/api/data")[1])
        if d3["candidates"]:
            cid3 = d3["candidates"][0]["id"]
            # 只有「明确反证」才否决。gates 里缺哪一项都不算被否 ——
            # 那只是 AI 这次没核到，留给人工确认。
            gated = dict(draft, gates=[], gates_denied=[sch["gates"][0]["key"]])
            st, body = areq("POST", "/api/candidates/%s/promote" % urllib.parse.quote(cid3),
                            {"verification_config": gated})
            check("门槛被否决时 409（与质量分无关）", st == 409, "实际 %d" % st)
            err3 = json.loads(body).get("error", "") if st == 409 else ""
            # 服务端报的是具体哪道门槛被否 —— 让用户一眼看到要查哪一项
            check("拦截时点名是被否的门槛",
                  ("门槛未过" in err3) and (sch["gates"][0]["label"] in err3), err3[:48])

            # 反向：拿不准不等于被否，仍可发布，但要挂在「待人工复核」里
            st, body = areq("PUT", "/api/candidates/%s/verification"
                            % urllib.parse.quote(cid3), dict(draft, gates=[]))
            rr = json.loads(body).get("result", {}) if st == 200 else {}
            check("门槛拿不准仍可发布（交人工复核）",
                  rr.get("publishable") is True, "-> %s" % rr.get("verdict", body[:56]))
            check("拿不准的门槛计入待人工复核",
                  len(rr.get("unverified_gates") or []) == len(sch["gates"]),
                  "unverified=%d" % len(rr.get("unverified_gates") or []))

            # 本轮口径：一道成立就进库 —— AI 的反证降级成「待人工复核」提醒
            rescued = dict(draft, gates=[sch["gates"][0]["key"]],
                           gates_denied=[sch["gates"][1]["key"]])
            st, body = areq("PUT", "/api/candidates/%s/verification"
                            % urllib.parse.quote(cid3), rescued)
            rr2 = json.loads(body).get("result", {}) if st == 200 else {}
            check("一道成立就放行（反证不再一票否决）",
                  rr2.get("publishable") is True, "-> %s" % rr2.get("verdict", body[:56]))
            check("被放行的反证仍点名出来",
                  any(w.get("key") == "denied_" + sch["gates"][1]["key"]
                      for w in (rr2.get("warnings") or [])),
                  "warnings=%s" % [w.get("key") for w in (rr2.get("warnings") or [])])

            # 三道门槛全成立 → 质量分自动加成（不是手勾项，是由 gates 推导）
            _all = [g["key"] for g in sch["gates"]]
            none = dict(draft, gates=[], bonus=[])
            allg = dict(draft, gates=_all, bonus=[])
            st, body = areq("PUT", "/api/candidates/%s/verification"
                            % urllib.parse.quote(cid3), none)
            rn = json.loads(body).get("result", {}) if st == 200 else {}
            st, body = areq("PUT", "/api/candidates/%s/verification"
                            % urllib.parse.quote(cid3), allg)
            ra = json.loads(body).get("result", {}) if st == 200 else {}
            check("三道全成立自动加质量分",
                  ra.get("gate_bonus") == 10 and rn.get("gate_bonus") == 0,
                  "全=%s 无=%s" % (ra.get("gate_bonus"), rn.get("gate_bonus")))
            check("加成计入得分且不动上限",
                  ra.get("bonus_score") == rn.get("bonus_score") + 10 and
                  ra.get("bonus_max") == rn.get("bonus_max"),
                  "%s/%s vs %s/%s" % (rn.get("bonus_score"), rn.get("bonus_max"),
                                      ra.get("bonus_score"), ra.get("bonus_max")))
        else:
            check("门槛被否决时 409（与质量分无关）", True, "（候选池已空，跳过）")
            check("拦截时点名是被否的门槛", True, "（候选池已空，跳过）")
            check("门槛拿不准仍可发布（交人工复核）", True, "（候选池已空，跳过）")
            check("拿不准的门槛计入待人工复核", True, "（候选池已空，跳过）")
            check("一道成立就放行（反证不再一票否决）", True, "（候选池已空，跳过）")
            check("被放行的反证仍点名出来", True, "（候选池已空，跳过）")
            check("三道全成立自动加质量分", True, "（候选池已空，跳过）")
            check("加成计入得分且不动上限", True, "（候选池已空，跳过）")

        # ---------- 6 新增 + 修改 ----------
        print("\n[6] 写操作 · 新增与修改")
        st, body = areq("POST", "/api/cases",
                        {"name": "Selftest Widget", "one_liner": "自测临时条目", "category": "自测"})
        check("POST /api/cases", st == 201 and json.loads(body)["case"]["id"] == "selftest-widget")
        st, body = areq("PATCH", "/api/cases/selftest-widget",
                       {"verification": "partial", "verdict": "已改"})
        check("PATCH /api/cases/<id>", st == 200 and json.loads(body)["case"]["verification"] == "partial")
        st, body = areq("POST", "/api/cases", {})
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
            st, _ = areq(method, path, {} if method == "POST" else None)
            check("%s %s" % (method, path), st == want, "实际 %d" % st)

        # 目录穿越（urllib 会先做一次归一化，所以 403/404 都算拦住了）
        st, _ = req("GET", "/../../server.py")
        check("GET /../../server.py 被拦", st in (403, 404), "实际 %d" % st)
        st, body = req("GET", "/api/data")
        check("越界后服务仍正常", st == 200, "(%d)" % st)

        # ---------- 8 后台鉴权 ----------
        # 上面那台服务关了鉴权，这里另起一台「带鉴权」的，验证闸门真的关得上：
        # 游客碰不到任何写接口，登录后才放行，登出后立刻失效。
        run_auth_section()

        # ---------- 9 双站点隔离 ----------
        # 注意这台服务是关了鉴权的（CASE_LIB_NO_AUTH=1）。
        # 所以这一节验的不是「没口令进不去」，而是「公开站上压根没有这些路由」——
        # 就算鉴权整个关掉、就算带着后台的 cookie，公开站也改不了库。
        # 「两个站分开」必须是结构性的，不能只靠鉴权撑着。
        print("\n[9] 双站点隔离（关掉鉴权后公开站仍然只读）")
        for method, path, payload in [
                ("POST", "/api/login", {"password": "whatever"}),
                ("POST", "/api/logout", {}),
                ("GET", "/api/verify-schema", None),
                ("PUT", "/api/candidates/x/verification", {}),
                ("POST", "/api/candidates/x/promote", {}),
                ("POST", "/api/inbox/x/to-candidates", {}),
                ("PATCH", "/api/cases/x/tier", {}),
                ("POST", "/api/cases", {"name": "隔离测试"}),
        ]:
            st, _ = req(method, path, payload)
            check("公开站 %s %s -> 404" % (method, path), st == 404, "实际 %d" % st)

        st, body = req("GET", "/app.js")
        check("公开站脚本里没有后台代码",
              st == 200 and b"openVerify" not in body and b"IS_ADMIN" not in body
              and b"/api/login" not in body and b"renderAdminChip" not in body)
        st, body = req("GET", "/style.css")
        check("公开站样式里没有后台样式",
              st == 200 and b".adm-nav" not in body and b".admin-btn" not in body)
        st, body = req("GET", "/")
        check("公开站首页没有后台入口", st == 200 and b"admin-btn" not in body)
        st, _ = req("GET", "/verify.js")
        check("公开站取不到后台脚本（目录不同）", st == 404)
        st, _ = req("GET", "/app.js")
        check("公开站的 app.js 是公开站那份", st == 200)

        st, body = req("GET", "/api/data")
        check("公开站永远拿不到核实草稿",
              st == 200 and json.loads(body).get("verifications") == {},
              "实际 %s" % (json.loads(body).get("verifications"),))
        st, body = areq("GET", "/api/data")
        check("后台拿得到核实草稿", st == 200 and "verifications" in json.loads(body))
        # 上面第 5 节刚存过草稿，所以这里必须非空，否则这条断言等于没测
        st, body = areq("GET", "/api/data")
        check("（草稿确实存在，上面的对比才有意义）",
              isinstance(json.loads(body).get("verifications"), dict))

        # ---------- [10] AI 自动核实端点 ----------
        # 只测不依赖真实 LLM 的部分：plan（离线规则引擎）、settings 存取、
        # run 的前置校验与任务生命周期。映射逻辑由 scripts/test_ai_verify.py 覆盖。
        print("\n[10] AI 自动核实端点")
        st, body = areq("GET", "/api/ai/plan")
        plan = json.loads(body) if st == 200 else {}
        check("后台 plan -> 200 且结构齐全", st == 200
              and isinstance(plan.get("items"), list) and plan.get("total", 0) > 0,
              "实际 %s" % st)
        if plan.get("items"):
            it = plan["items"][0]
            check("plan 条目带卡点字段",
                  all(k in it for k in ("id", "name", "gate_keys", "must_keys", "verdict")))
        check("plan 里没有占位条目（（ 开头的已跳过）",
              all(not it["id"].startswith("（") for it in plan.get("items", [])))

        st, _ = req("GET", "/api/ai/plan")
        check("公开站 plan -> 404", st == 404, "实际 %d" % st)

        st, body = areq("GET", "/api/ai/status")
        status = json.loads(body) if st == 200 else {}
        check("后台 status -> 200 且 job.state=idle", st == 200
              and status.get("job", {}).get("state") == "idle", "实际 %s" % body[:80])
        check("status 报告 has_key 字段（本机可能已配过 key，只验类型）",
              isinstance(status.get("has_key"), bool))

        if not status.get("has_key"):
            st, body = areq("POST", "/api/ai/run", {"limit": 3})
            check("没配 key 时 run -> 400", st == 400, "实际 %d %s" % (st, body[:60]))
        else:
            print("  （本机已配置 key，跳过 400 分支）")

        # 保存 / 更换 / 清除 key（不触发任何网络请求）
        st, body = areq("POST", "/api/ai/settings", {"api_key": "sk-selftest-xxx",
                                                     "ai_model": "test-model"})
        check("保存 key -> ok", st == 200 and json.loads(body).get("has_key") is True,
              "实际 %s" % body[:80])
        st, body = areq("GET", "/api/ai/status")
        status = json.loads(body)
        check("保存后 status.has_key=True 且模型生效",
              status.get("has_key") is True and status.get("model") == "test-model",
              "实际 %s" % status.get("model"))
        # 用假 key 跑一条（卡点/检索不花钱，LLM 那步会 401 失败收场）
        cid0 = (plan.get("items") or [{}])[0].get("id")
        st, body = areq("POST", "/api/ai/run", {"ids": [cid0]} if cid0 else {"limit": 1})
        check("配了 key 后 run 能启动", st == 200
              and json.loads(body).get("state") == "running", "实际 %d" % st)
        st, body = areq("POST", "/api/ai/run", {"limit": 3})
        check("任务运行中重复 run -> 409", st == 409, "实际 %d" % st)
        # 等任务自然结束（假 key 下检索可能真联网，最多等 150 秒）
        deadline = time.time() + 150
        final = {}
        while time.time() < deadline:
            st, body = areq("GET", "/api/ai/status")
            final = json.loads(body)
            if final.get("job", {}).get("state") != "running":
                break
            time.sleep(1)
        check("任务最终结束（done/error，日志非空）",
              final.get("job", {}).get("state") in ("done", "error")
              and len(final.get("job", {}).get("log") or []) > 0,
              "实际 state=%s" % final.get("job", {}).get("state"))

        st, body = areq("POST", "/api/ai/settings", {"api_key": ""})
        check("清空 key -> has_key=False", st == 200
              and json.loads(body).get("has_key") is False)
        st, body = areq("POST", "/api/ai/settings", {"ai_base": "ftp://bad"})
        check("非法接口地址 -> 400", st == 400)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

        # ---------- 还原 ----------
        print("\n[*] 还原 data/")
        # 备份里有没有 secrets.json 必须在 rmtree 之前判断——
        # 曾经把判断写在 rmtree 之后，永远为真，结果把用户真实的
        # secrets.json（里面的真 key）当测试残留删掉了。
        had_secrets_in_backup = os.path.exists(os.path.join(BACKUP, "secrets.json"))
        for name in os.listdir(BACKUP):
            src = os.path.join(BACKUP, name)
            dst = os.path.join(DATA, name)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
        shutil.rmtree(BACKUP)
        # 测试期间新建的文件不在备份里，还原盖不掉 —— 会把假 key 留给用户
        if not had_secrets_in_backup \
                and os.path.exists(os.path.join(DATA, "secrets.json")):
            os.remove(os.path.join(DATA, "secrets.json"))
            print("    已清掉测试残留的 data/secrets.json")
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
