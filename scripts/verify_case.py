#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对**已发布案例**跑一次 AI 自核，看它够不够精品档。

用法
----
    python scripts/verify_case.py <case_id>          # 例：gojiberryai
    python scripts/verify_case.py <case_id> --keep   # 核实后把候选留在池子里

为什么需要这个脚本
------------------
`ai_verify.py` 只能核**候选池**里的条目，而案例一旦被提升就不在候选池里了。
所以流程是：

  1. 按案例里的真实材料重建一条候选（只是让它能被核实接口接受）
  2. 用本地 secret 铸一个合法 admin cookie（不用明文密码、不用关鉴权）
  3. 调真的后台接口跑 verify_one —— 规则引擎是服务端那份，结论才作数

默认跑完把候选池还原（插入候选只是脚手架），草稿留在 `data/verifications.json`
当证据。

三个踩过的坑
------------
- Admin 走的是 127.0.0.1，**必须绕开系统代理**，否则被拦成 502。
- 候选要带上 `sources`，否则 `known_urls()` 拿不到已知地址，AI 只能靠搜索 ——
  而搜索对小众产品完全无效（Bing 查 GojiberryAI 返回的是「无结果」兜底页）。
- **不要把案例原有的 `corrections` 塞进 `cand_brief`** —— 那等于把答案递给 AI，
  它会照抄而不是独立核实。`sources` 只当抓取清单用。

依赖：仅 Python 标准库。需要 `server.py` 已在跑（后台端口 5053）。
"""

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auth                                                            # noqa: E402
import ai_verify as AV                                                 # noqa: E402
import verify_rules as VR                                              # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
KEEP = "--keep" in sys.argv


def main():
    if not ARGS:
        print(__doc__)
        print("错误：要给一个 case_id，例如 gojiberryai")
        return 2
    cid = ARGS[0]
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    # ---------------------------------------------------------- 1. 备份
    bak_dir = os.path.join(ROOT, "_backfill_backup")
    os.makedirs(bak_dir, exist_ok=True)
    for f in ("candidates", "verifications", "cases"):
        src = os.path.join(ROOT, "data", f + ".json")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(bak_dir, "%s.json.bak-%s" % (f, ts)))
    print("[1] 已备份 data/{candidates,verifications,cases}.json → %s" % ts)

    # ---------------------------------------------------------- 2. 造候选
    with open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8") as f:
        case = next((c for c in json.load(f) if c.get("id") == cid), None)
    if not case:
        print("[!] cases.json 里找不到 %s" % cid)
        return 1

    site = next((s["url"] for s in (case.get("sources") or [])
                 if isinstance(s, dict) and s.get("kind") == "official"), "")
    cand = {
        "id": cid,
        "name": case.get("name") or cid,
        "name_en": case.get("name_en") or case.get("name") or cid,
        "origin": case.get("origin") or "未披露",
        "one_liner": case.get("one_liner") or "",
        "category": case.get("category") or "未分类",
        "verification": "unverified",
        "metrics": case.get("metrics") or {},
        "models": case.get("models") or [],
        "note": "来源见案例里登记的 sources。",
        "blocking": "需确认收入口径与数据时效",
        "source_url": (case.get("source_url")
                       or next((s.get("url") for s in (case.get("sources") or [])
                                if isinstance(s, dict)), "")),
        "website": site,
        # 只当抓取清单用 —— 不进 cand_brief，所以 LLM 看不到「现成结论」。
        "sources": case.get("sources") or [],
        "harvest_source": "trustmrr" if "trustmrr" in json.dumps(
            case.get("sources") or [], ensure_ascii=False) else "",
        "added_at": datetime.now().strftime("%Y-%m-%d"),
    }
    cand["source_kind"] = source_kind_of(cand)

    cpath = os.path.join(ROOT, "data", "candidates.json")
    with open(cpath, encoding="utf-8") as f:
        cands = json.load(f)
    cands = [c for c in cands if c.get("id") != cid]
    cands.insert(0, cand)
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(cands, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("[2] 已插入候选 %s（共 %d 条）" % (cid, len(cands)))

    # ---------------------------------------------------------- 3. 铸 cookie
    secret = auth.get_secret(auth.load_admin())
    cookie = auth.cookie_header(auth.make_token(secret))
    print("[3] 已用本地 secret 铸好 admin cookie")

    noproxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _req(self, method, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Cookie": self.cookie})
        try:
            with noproxy.open(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8")), r.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8")), e.code
            except Exception:
                return {"error": "HTTP %s" % e.code}, e.code

    AV.Admin._req = _req
    admin = AV.Admin(AV.ADMIN_BASE, cookie=cookie)
    print("[4] 已连接后台 %s" % AV.ADMIN_BASE)

    # ---------------------------------------------------------- 4. AI key
    with open(os.path.join(ROOT, "data", "secrets.json"), encoding="utf-8") as f:
        sec = json.load(f)
    AV.configure(api_key=sec.get("ai_key"), base=sec.get("ai_base"),
                 model=sec.get("ai_model"))
    print("[5] AI: %s（key 长度 %d）" % (AV.AI_MODEL, len(AV.AI_KEY or "")))
    print()
    print("=" * 64)
    print("  开始 AI 自核：%s" % cid)
    print("=" * 64)

    out = AV.verify_one(cand, admin, publish=False)

    print()
    print("=" * 64)
    print("  判定结果")
    print("=" * 64)
    print("  verdict      : %s" % out.get("verdict"))
    print("  质量分       : %s / %d" % (out.get("score"), VR.TIER_THRESHOLD))
    print("  档位         : %s (%s)" % (out.get("tier"), out.get("tier_label")))
    print("  核实等级     : %s" % out.get("verification"))
    print("  口径         : %s" % out.get("caliber"))
    print("  抓到原文页数 : %s" % out.get("pages"))
    print("  来源条数     : %s" % out.get("source_count"))
    for key, label in (("unverified", "门槛未确认"), ("denied", "明确反证"),
                       ("warnings", "提醒"), ("remain", "还差")):
        vals = out.get(key) or []
        if vals:
            print("  %-12s : %s" % (label, "；".join(str(v) for v in vals)))
    print()
    score = out.get("score") or 0
    if out.get("tier") == VR.TIER_PREMIUM:
        print("  → 够精品档（%d ≥ %d）" % (score, VR.TIER_THRESHOLD))
    else:
        print("  → 不够精品档：%d < %d，差 %d 分"
              % (score, VR.TIER_THRESHOLD, VR.TIER_THRESHOLD - score))
    print()
    print("注：本轮没有 publish（未改 cases.json）。草稿在 data/verifications.json。")

    # ---------------------------------------------------------- 5. 清场
    if KEEP:
        print("    候选 %s 已保留在候选池（--keep）" % cid)
    else:
        shutil.copy2(os.path.join(bak_dir, "candidates.json.bak-%s" % ts), cpath)
        n = len(json.load(open(cpath, encoding="utf-8")))
        print("    候选池已还原为 %d 条（--keep 可留下）" % n)
    return 0


def source_kind_of(rec):
    """按 verify_rules.first_hand_kinds 推一手性质，只为候选字段完整。"""
    return "verified" if VR.first_hand_kinds(rec) else "discovery"


if __name__ == "__main__":
    sys.exit(main())
