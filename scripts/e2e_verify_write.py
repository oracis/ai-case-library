# -*- coding: utf-8 -*-
"""端到端验证「核实写入路径」：PUT /verification → promote → 案例字段。

为什么必须走真服务端：铸时间戳、抄到案例上、字段白名单，这几步都在
server.py 里。单测绕过 HTTP 层是抓不到的 —— 本项目踩过这个坑：草稿字段
漏进白名单，存盘即丢，而所有单测都是绿的。

它验证的是「一个字段有没有真的活到案例上」，所以加/改核实草稿的字段时
都该跑一遍：先写完，再确认落盘。

    python server.py                                  # 另开一个终端，先起服务
    python scripts/e2e_verify_write.py

用合成候选跑，跑完从备份还原 data/{candidates,verifications,cases}.json，
不留痕迹。**它不参与 CI**（需要真服务端），是给人手动跑的工具。
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auth as AUTH                                                  # noqa: E402

PORT = 5053
HOST = "http://127.0.0.1:%d" % PORT
DATA = os.path.join(ROOT, "data")
FILES = ["candidates.json", "verifications.json", "cases.json"]
PROBE1, PROBE2, PROBE3 = "__hr_probe_a__", "__hr_probe_b__", "__hr_probe_c__"

passed, fails = [], []


def chk(name, cond, extra=""):
    if cond:
        passed.append(name)
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def opener():
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
            "ALL_PROXY", "all_proxy")
    if any(os.environ.get(k) for k in keys):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


OP = opener()
COOKIE = ""


def req(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(HOST + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if COOKIE:
        r.add_header("Cookie", COOKIE)
    try:
        with OP.open(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:                                            # noqa: BLE001
            return e.code, {"raw": raw[:300]}
    except Exception as e:                                           # noqa: BLE001
        return -1, {"error": str(e)}


def draft(**over):
    d = {
        "gates": ["still_alive", "is_business", "solo_possible"],
        "gates_denied": [],
        "musts": ["caliber_decided", "caliber_consistent"],
        "bonus": [],
        "caliber": "mrr",
        "verification": "stripe",
        "source_kinds": ["stripe"],
        "sources": [{"label": "TrustMRR", "url": "https://trustmrr.com/startup/x",
                     "kind": "stripe"}],
        "corrections": [],
        "note": "",
    }
    d.update(over)
    return d


def probe_candidate(pid, name):
    return {
        "id": pid, "name": name, "name_en": name, "origin": "美国",
        "one_liner": "端到端探针（跑完即删）", "category": "探针",
        "verification": "unverified", "metrics": {}, "sources": [],
        "harvest_source": "trustmrr", "source_kind": "verified",
        "added_at": datetime.now().strftime("%Y-%m-%d"),
    }


def main():
    global COOKIE
    tmp = tempfile.mkdtemp(prefix="hr_e2e_")
    for f in FILES:
        shutil.copy2(os.path.join(DATA, f), os.path.join(tmp, f))
    print("已备份 data/{%s} → %s\n" % (",".join(FILES), tmp))

    try:
        cfg = AUTH.load_admin()
        COOKIE = AUTH.COOKIE_NAME + "=" + AUTH.make_token(AUTH.get_secret(cfg))
        st, _ = req("GET", "/api/data")
        chk("带服务端铸的 cookie 能读后台", st == 200, "HTTP %s" % st)

        cands = json.load(open(os.path.join(DATA, "candidates.json"), encoding="utf-8"))
        cands = [c for c in cands if c.get("id") not in (PROBE1, PROBE2, PROBE3)]
        cands.insert(0, probe_candidate(PROBE1, "HR Probe A"))
        cands.insert(0, probe_candidate(PROBE2, "HR Probe B"))
        cands.insert(0, probe_candidate(PROBE3, "HR Probe C"))
        json.dump(cands, open(os.path.join(DATA, "candidates.json"), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=2)

        print("\n[1] PUT /verification —— 勾了人工核读，且伪造时间戳")
        body = draft(musts=["caliber_decided", "caliber_consistent", "human_read"],
                     human_read_at="1999-01-01 00:00")
        st, j = req("PUT", "/api/candidates/%s/verification" % PROBE1, body)
        chk("存草稿成功", st == 200, "HTTP %s" % st)
        got = (j.get("draft") or {}).get("human_read_at", "")
        chk("服务端铸了时间戳", bool(got), repr(got))
        chk("body 里伪造的 1999 被忽略", not got.startswith("1999"), repr(got))
        chk("判定结果 human_read=True", (j.get("result") or {}).get("human_read") is True)
        stamp_a = got
        stored = json.load(open(os.path.join(DATA, "verifications.json"), encoding="utf-8"))
        chk("时间戳落盘了", bool(stored.get(PROBE1, {}).get("human_read_at")), "")

        print("\n[2] 再次 PUT（不带 human_read_at）—— 时间戳不该被清掉")
        st, j = req("PUT", "/api/candidates/%s/verification" % PROBE1,
                    draft(musts=["caliber_decided", "caliber_consistent", "human_read"]))
        chk("时间戳保持不变", (j.get("draft") or {}).get("human_read_at") == stamp_a,
            repr((j.get("draft") or {}).get("human_read_at")))

        print("\n[3] 取消勾选 —— 时间戳要被清掉")
        st, j = req("PUT", "/api/candidates/%s/verification" % PROBE1,
                    draft(musts=["caliber_decided", "caliber_consistent"]))
        chk("取消后时间戳清空", (j.get("draft") or {}).get("human_read_at") == "",
            repr((j.get("draft") or {}).get("human_read_at")))
        chk("取消后 human_read=False", (j.get("result") or {}).get("human_read") is False)

        print("\n[4] 重新勾上，然后提升成案例")
        st, j = req("PUT", "/api/candidates/%s/verification" % PROBE1,
                    draft(musts=["caliber_decided", "caliber_consistent", "human_read"]))
        stamp_a = (j.get("draft") or {}).get("human_read_at", "")
        chk("重新勾上又有时间戳", bool(stamp_a), repr(stamp_a))
        chk("未核读时也可发布（不拦）", (j.get("result") or {}).get("publishable") is True)

        st, j = req("POST", "/api/candidates/%s/promote" % PROBE1,
                    {"verification_config": draft(
                        musts=["caliber_decided", "caliber_consistent", "human_read"])})
        chk("提升成功", st == 201, "HTTP %s %s" % (st, j.get("error", "")))
        case = j.get("case") or {}
        chk("案例上 human_read=True", case.get("human_read") is True, repr(case.get("human_read")))
        chk("案例上时间戳 = 勾选时刻", case.get("human_read_at") == stamp_a,
            "%r vs %r" % (case.get("human_read_at"), stamp_a))

        print("\n[5] 没勾人工核读的那条 —— 也该能进库，但要标明未核读")
        st, j = req("PUT", "/api/candidates/%s/verification" % PROBE2,
                    draft(musts=["caliber_decided", "caliber_consistent"]))
        chk("不勾也能发布", (j.get("result") or {}).get("publishable") is True, "")
        chk("挂了 pending_human_read 提醒",
            "pending_human_read" in [w.get("key") for w in
                                     (j.get("result") or {}).get("warnings", [])])
        st, j = req("POST", "/api/candidates/%s/promote" % PROBE2,
                    {"verification_config": draft(
                        musts=["caliber_decided", "caliber_consistent"])})
        chk("提升成功", st == 201, "HTTP %s %s" % (st, j.get("error", "")))
        case2 = j.get("case") or {}
        chk("案例上 human_read=False", case2.get("human_read") is False,
            repr(case2.get("human_read")))
        chk("案例上时间戳为空", case2.get("human_read_at") == "",
            repr(case2.get("human_read_at")))

        print("\n[6] 通用 PATCH 不能伪造核读状态")
        cid = case2.get("id")
        st, j = req("PATCH", "/api/cases/%s" % cid,
                    {"human_read": True, "human_read_at": "2000-01-01 00:00"})
        chk("PATCH 请求本身成功", st == 200, "HTTP %s" % st)
        chk("human_read 没被改写", (j.get("case") or {}).get("human_read") is False,
            repr((j.get("case") or {}).get("human_read")))
        chk("human_read_at 没被改写",
            (j.get("case") or {}).get("human_read_at") == "",
            repr((j.get("case") or {}).get("human_read_at")))

        print("\n[7] 等级不能被虚标 —— 声称 stripe 但只有第三方来源")
        # 曾经 11 条案例就这样进了库：标着「支付网关验证」，来源却只有 press/review。
        # 等级是给读者的信任凭证，虚标等于替他们做了一次没授权的背书。
        thin = draft(verification="stripe", source_kinds=["press"],
                     sources=[{"label": "某第三方拆解站", "url": "https://x.example/",
                               "kind": "press"}])
        st, j = req("POST", "/api/candidates/%s/promote" % PROBE3,
                    {"verification_config": thin})
        chk("虚标也被允许发布（门槛不看等级）", st == 201,
            "HTTP %s %s" % (st, j.get("error", "")))
        case3 = j.get("case") or {}
        chk("等级被降到证据撑得住的那档", case3.get("verification") == "partial",
            repr(case3.get("verification")))
        dg = case3.get("verification_downgraded") or {}
        chk("留了降级记录（原值可还原）", dg.get("from") == "stripe",
            json.dumps(dg, ensure_ascii=False)[:80])
        chk("降级理由写明缺哪种来源", "press" in (dg.get("reason") or ""),
            (dg.get("reason") or "")[:60])

        print("\n[8] 校验数据文件里真的存下来了（不是只回包里有）")
        found3 = [c for c in json.load(open(os.path.join(DATA, "cases.json"),
                                            encoding="utf-8"))
                  if c.get("published_from") == PROBE3]
        chk("降级后的案例落了盘", len(found3) == 1 and
            found3[0].get("verification") == "partial", len(found3))

        cases = json.load(open(os.path.join(DATA, "cases.json"), encoding="utf-8"))
        on_disk = {c.get("id"): c for c in cases}
        found = [c for c in cases if c.get("published_from") in (PROBE1, PROBE2)]
        chk("两个探针案例都落了盘", len(found) == 2, len(found))
        for c in found:
            chk("案例 %s 带 human_read 字段" % c.get("id"),
                "human_read" in c and "human_read_at" in c,
                "human_read=%r at=%r" % (c.get("human_read"), c.get("human_read_at")))
        chk("有核读时刻的就是 True", any(c.get("human_read") for c in found))
        chk("没核读的就是 False", any(c.get("human_read") is False for c in found))

    finally:
        for f in FILES:
            shutil.copy2(os.path.join(tmp, f), os.path.join(DATA, f))
        shutil.rmtree(tmp, ignore_errors=True)
        print("\n已从备份还原 data/{%s}" % ",".join(FILES))

    print("\n" + "=" * 62)
    print("  结果：%d 通过 / %d 失败" % (len(passed), len(fails)))
    if fails:
        print("  失败项：" + "；".join(fails))
    print("=" * 62)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
