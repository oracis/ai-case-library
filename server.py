#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 赚钱案例库 (AI Case Library) · 本地服务

零依赖：只用 Python 标准库，不需要 pip install 任何东西。
启动：  python server.py        (默认 http://127.0.0.1:5052)
自定义端口：set CASE_LIB_PORT=5099 && python server.py
"""

import json
import os
import re
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")
DATA_DIR = os.path.join(ROOT, "data")
PORT = int(os.environ.get("CASE_LIB_PORT", "5052"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
}

# 写操作串行化，避免并发写坏 json
_LOCK = threading.Lock()


def data_path(name):
    return os.path.join(DATA_DIR, name + ".json")


def load_json(name):
    path = data_path(name)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(name, payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = data_path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def slugify(text, taken=None):
    """生成 URL 友好的 id"""
    taken = taken or set()
    base = re.sub(r"[^a-z0-9]+", "-", (text or "case").lower()).strip("-") or "case"
    if base not in taken:
        return base
    i = 2
    while "%s-%d" % (base, i) in taken:
        i += 1
    return "%s-%d" % (base, i)


# 四象限的文案。与 scripts/score_solo_fit.py 里的 QUADRANTS 保持一致。
QUAD_META = {
    "go": ("可以开干", "两边都过线：一个人能做，国内也有市场。"),
    "export": ("能做，但别在国内卖", "技术完全在手，卡在国内的需求或支付土壤上。出口做更顺。"),
    "partner": ("有市场，但一个人啃不动", "需求是真的，门槛在资质、大客户销售或团队交付上。"),
    "skip": ("别碰", "两个方向都不过线。"),
}


def score_card(c, field):
    """把 cases[].<field> 里的评分展开成前端卡片需要的扁平结构。"""
    s = c.get(field) or {}
    card = {
        "id": c.get("id"),
        "name": c.get("name"),
        "category": c.get("category"),
        "rank": s.get("rank"),
        "medal": s.get("medal"),
        "score": s.get("score"),
        "note": s.get("note") or s.get("delivery_note"),
        "blocker": s.get("blocker"),
        "dims": s.get("dims"),
        "weights": s.get("weights"),
    }
    if field == "composite":
        card.update({
            "solo": s.get("solo"),
            "china": s.get("china"),
            "quadrant": s.get("quadrant"),
            "quadrant_label": s.get("quadrant_label"),
        })
    return card


def build_payload():
    cases = load_json("cases")
    candidates = load_json("candidates")
    inbox = load_json("inbox")
    sources = load_json("sources")

    # 统计
    by_verification = {}
    by_category = {}
    by_model = {}
    for c in cases:
        v = c.get("verification", "unverified")
        by_verification[v] = by_verification.get(v, 0) + 1
        cat = c.get("category", "未分类")
        by_category[cat] = by_category.get(cat, 0) + 1
        for m in c.get("models", []):
            by_model[m] = by_model.get(m, 0) + 1

    # 已核实 = stripe + official
    verified = by_verification.get("stripe", 0) + by_verification.get("official", 0)

    # 国内移植可行性排行（由 scripts/score_china_fit.py 写入 cases[].china_fit）
    china_ranked = [c for c in cases if isinstance(c.get("china_fit"), dict)]
    china_ranked.sort(key=lambda c: c["china_fit"].get("rank", 999))
    china_top3 = [
        {
            "rank": c["china_fit"].get("rank"),
            "medal": c["china_fit"].get("medal"),
            "id": c.get("id"),
            "name": c.get("name"),
            "category": c.get("category"),
            "score": c["china_fit"].get("score"),
            "note": c["china_fit"].get("note"),
            "blocker": c["china_fit"].get("blocker"),
            "dims": c["china_fit"].get("dims"),
        }
        for c in china_ranked[:3]
    ]

    # 个人可做性排行（由 scripts/score_solo_fit.py 写入 cases[].solo_fit）
    solo_ranked = [c for c in cases if isinstance(c.get("solo_fit"), dict)]
    solo_ranked.sort(key=lambda c: c["solo_fit"].get("rank", 999))
    solo_top3 = [score_card(c, "solo_fit") for c in solo_ranked[:3]]

    # 双轴综合（cases[].composite）
    dual_ranked = [c for c in cases if isinstance(c.get("composite"), dict)]
    dual_ranked.sort(key=lambda c: c["composite"].get("rank", 999))
    dual_top3 = [score_card(c, "composite") for c in dual_ranked[:3]]

    # 四象限分组（按重要性降序：能开干 → 要合伙 → 做海外 → 别碰）
    quadrants = []
    for key in ("go", "partner", "export", "skip"):
        grp = [c for c in dual_ranked if c["composite"].get("quadrant") == key]
        label, desc = QUAD_META.get(key, (key, ""))
        quadrants.append({
            "key": key,
            "label": label,
            "desc": desc,
            "count": len(grp),
            "cases": [score_card(c, "composite") for c in grp],
        })

    stats = {
        "curated": len(cases),
        "candidates": len(candidates),
        "inbox": len(inbox),
        "verified": verified,
        "china_scored": len(china_ranked),
        "china_blocked": sum(1 for c in china_ranked if c["china_fit"].get("blocker")),
        "china_top3": china_top3,
        "solo_scored": len(solo_ranked),
        "solo_top3": solo_top3,
        "dual_scored": len(dual_ranked),
        "dual_top3": dual_top3,
        "quadrants": quadrants,
        "flagged": sum(
            len(c.get("corrections", [])) for c in cases
        ) + sum(1 for c in cases if c.get("verification") == "disputed"),
        "categories": len(by_category),
        "by_verification": by_verification,
        "by_category": by_category,
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])),
    }

    return {
        "cases": cases,
        "candidates": candidates,
        "inbox": inbox,
        "sources": sources,
        "stats": stats,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "CaseLibrary/1.0"

    def log_message(self, fmt, *args):  # 静音默认日志
        if os.environ.get("CASE_LIB_VERBOSE"):
            sys.stderr.write("[case-lib] " + (fmt % args) + "\n")

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/data":
            return self._json(build_payload())
        if path == "/api/stats":
            return self._json(build_payload()["stats"])
        if path == "/api/ping":
            return self._json({"ok": True, "port": PORT})

        # 静态文件
        if path in ("/", ""):
            path = "/index.html"
        rel = unquote(path).lstrip("/").replace("..", "")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.normpath(STATIC_DIR)):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")

        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    def do_PATCH(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/cases/([^/]+)$", path)
        if not m:
            return self._json({"error": "unknown route"}, 404)

        cid = unquote(m.group(1))
        patch = self._read_body()
        with _LOCK:
            cases = load_json("cases")
            for c in cases:
                if c.get("id") == cid:
                    for k, v in patch.items():
                        if k == "id":
                            continue
                        c[k] = v
                    c["updated_at"] = datetime.now().strftime("%Y-%m-%d")
                    save_json("cases", cases)
                    return self._json({"ok": True, "case": c})
        return self._json({"error": "case not found: " + cid}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        # 采集队列 -> 候选池（人工筛选中转站）
        m = re.match(r"^/api/inbox/([^/]+)/to-candidates$", path)
        if m:
            cid = unquote(m.group(1))
            with _LOCK:
                inbox = load_json("inbox")
                candidates = load_json("candidates")
                hit, rest = None, []
                for c in inbox:
                    if c.get("id") == cid and hit is None:
                        hit = c
                    else:
                        rest.append(c)
                if hit is None:
                    return self._json({"error": "inbox item not found: " + cid}, 404)
                hit["id"] = slugify(hit.get("name_en") or hit.get("name"),
                                    {c.get("id") for c in candidates})
                hit["promoted_from_inbox"] = datetime.now().strftime("%Y-%m-%d")
                candidates.insert(0, hit)
                save_json("candidates", candidates)
                save_json("inbox", rest)
                return self._json({"ok": True, "candidate": hit}, 201)

        # 候选 -> 精写案例
        m = re.match(r"^/api/candidates/([^/]+)/promote$", path)
        if m:
            cid = unquote(m.group(1))
            body = self._read_body()
            with _LOCK:
                candidates = load_json("candidates")
                cases = load_json("cases")
                hit = None
                rest = []
                for c in candidates:
                    if c.get("id") == cid and hit is None:
                        hit = c
                    else:
                        rest.append(c)
                if hit is None:
                    return self._json({"error": "candidate not found: " + cid}, 404)

                taken = {c.get("id") for c in cases}
                new_case = {
                    "id": slugify(hit.get("name_en") or hit.get("name"), taken),
                    "name": hit.get("name"),
                    "name_en": hit.get("name_en"),
                    "origin": hit.get("origin", ""),
                    "one_liner": hit.get("one_liner", ""),
                    "category": hit.get("category", "未分类"),
                    "industry": hit.get("industry", ""),
                    "status": "curated",
                    "verification": hit.get("verification", "unverified"),
                    "metrics": hit.get("metrics", {}),
                    "models": hit.get("models", []),
                    "replicability": hit.get("replicability", {}),
                    "verdict": hit.get("verdict", ""),
                    "what_it_does": hit.get("what_it_does") or hit.get("one_liner", ""),
                    "how_it_makes_money": hit.get("how_it_makes_money", ""),
                    "why_it_works": hit.get("why_it_works", []),
                    "playbook": hit.get("playbook", []),
                    "signals": hit.get("signals", []),
                    "corrections": hit.get("corrections", []),
                    "sources": hit.get("sources", []),
                    "verified_at": datetime.now().strftime("%Y-%m-%d"),
                    "updated_at": datetime.now().strftime("%Y-%m-%d"),
                    "tags": hit.get("tags", []),
                    "needs_review": True,
                }
                if body:
                    new_case.update({k: v for k, v in body.items() if k != "id"})
                cases.insert(0, new_case)
                save_json("cases", cases)
                save_json("candidates", rest)
                return self._json({"ok": True, "case": new_case}, 201)

        # 新增案例
        if path == "/api/cases":
            body = self._read_body()
            if not body.get("name"):
                return self._json({"error": "name is required"}, 400)
            with _LOCK:
                cases = load_json("cases")
                taken = {c.get("id") for c in cases}
                body["id"] = slugify(body.get("name_en") or body["name"], taken)
                body.setdefault("status", "curated")
                body.setdefault("verification", "unverified")
                body.setdefault("created_at", datetime.now().strftime("%Y-%m-%d"))
                body["updated_at"] = datetime.now().strftime("%Y-%m-%d")
                cases.insert(0, body)
                save_json("cases", cases)
                return self._json({"ok": True, "case": body}, 201)

        # 新增候选
        if path == "/api/candidates":
            body = self._read_body()
            if not body.get("name"):
                return self._json({"error": "name is required"}, 400)
            with _LOCK:
                candidates = load_json("candidates")
                taken = {c.get("id") for c in candidates}
                body["id"] = slugify(body.get("name_en") or body["name"], taken)
                body.setdefault("status", "candidate")
                body.setdefault("verification", "unverified")
                body.setdefault("added_at", datetime.now().strftime("%Y-%m-%d"))
                candidates.insert(0, body)
                save_json("candidates", candidates)
                return self._json({"ok": True, "candidate": body}, 201)

        return self._json({"error": "unknown route"}, 404)


def main():
    if not os.path.isdir(DATA_DIR):
        print("[!] 找不到 data 目录：%s" % DATA_DIR)
        return 1

    for name in ("cases", "candidates", "inbox", "sources"):
        try:
            load_json(name)
        except Exception as e:
            print("[!] data/%s.json 解析失败：%s" % (name, e))
            return 1

    url = "http://127.0.0.1:%d/" % PORT
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 56)
    print("  AI 赚钱案例库 · 已启动")
    print("  地址：%s" % url)
    print("  数据：%s" % DATA_DIR)
    print("  Ctrl+C 停止")
    print("=" * 56)

    if not os.environ.get("CASE_LIB_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
