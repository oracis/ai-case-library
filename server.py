#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拆解海外 (Overseas Teardowns) · 本地服务

零依赖：只用 Python 标准库，不需要 pip install 任何东西。

一个进程开两个端口，两个站彻底分开：

  公开站  http://127.0.0.1:5052/   static/   只读。看完就能关，
                                            也能整个 build 成静态站传 OSS。
  后台    http://127.0.0.1:5053/   admin/    要登录，核实与入库在这里。

分端口而不是分路径，是因为「同一个站上的 /admin 路径」永远挡不住好奇
的人去试 —— 它会响应、会返回 401，等于明晃晃告诉别人这儿有个后台。
分成两个端口之后，公开站上根本没有这些接口，请求过来就是 404，和请求
一个不存在的图片没有任何区别。

写接口（PUT / PATCH / POST）只在后台端口存在；公开站连 /api/login 都
没有，撞库都没地方撞。

启动：  python server.py        (公开 5052 / 后台 5053)
自定义端口：set CASE_LIB_PORT=5099 && python server.py
          set CASE_LIB_ADMIN_PORT=5100 && python server.py
"""

import json
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")
ADMIN_DIR = os.path.join(ROOT, "admin")
DATA_DIR = os.path.join(ROOT, "data")
PORT = int(os.environ.get("CASE_LIB_PORT", "5052"))
# 后台默认挨着公开站开一个端口。两个站只共用 data/*.json，不共用任何界面代码。
ADMIN_PORT = int(os.environ.get("CASE_LIB_ADMIN_PORT", str(PORT + 1)))

# 核实规则引擎：门槛 / 硬性必填 / 质量分三层判定。
# 前端从 /api/verify-schema 取同一份规则渲染界面，两边不可能算出不同结论。
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import verify_rules as VRULES                                     # noqa: E402
# 管理员鉴权：核实功能是后台功能，写接口全部要过这一关
import auth as AUTH                                               # noqa: E402
# AI 自动核实流水线（scripts/ai_verify.py）：plan 离线可用，run 要 LLM key
import ai_verify as AIV                                           # noqa: E402

# 对外数据契约（/api/data 与 dist/data.json）。字段说明见 docs/DATA_SCHEMA.md。
# 改字段名 / 类型 / 枚举值时要同步改这里和那份文档；只加新字段不用动。
SCHEMA_VERSION = "1.0"
# 正文字段的语言（BCP-47）。结构化字段与语言无关，第三方程序可直接消费。
CONTENT_LANG = "zh-CN"

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

# 管理员凭据在 main() 里初始化（可能要生成密码并提示），这里先留空
_ADMIN_CFG = {}
# 设为 True 时跳过所有鉴权，只给自测用 —— 免得测试依赖真实密码
_AUTH_DISABLED = False
# 已登出的 token。token 本身是无状态的（签名的过期时间），服务端不存 session，
# 所以「登出」默认没法让它立刻失效 —— 这是无状态方案唯一的短板。
# 这里补一个内存黑名单：登出即失效，重启自然清空（重启后反正也没人持有旧 cookie）。
_REVOKED = set()

# 登录失败计数：{来源 IP: [失败次数, 首次失败时间]}。
# 本站只监听 127.0.0.1，撞库威胁有限；但「密码可以无限次尝试」始终是个
# 不该留的口子 —— 尤其是有人把服务转发到局域网时。超过阈值就锁一段时间，
# 代价是输错几次要等一会儿，收益是暴力枚举变得不可行。
_LOGIN_FAILS = {}
LOGIN_MAX_FAILS = 5          # 连续失败几次后开始锁
LOGIN_LOCK_SECONDS = 300     # 锁 5 分钟
LOGIN_FAIL_WINDOW = 900      # 计数窗口：15 分钟内没有新失败就清零


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


# ---------------------------------------------------------------------------
# AI 自动核实：密钥保存 + 后台任务
# ---------------------------------------------------------------------------
# LLM 密钥放 data/secrets.json（.gitignore 已排除）。优先级：环境变量 >
# secrets 文件 —— 跟 .env 的习惯一致；后台 GUI 里保存的 key 会同时写进
# secrets 文件和本进程的环境变量，所以改完立刻生效、重启后也还在。
def load_secrets():
    path = data_path("secrets")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}          # 缺一个密钥文件不该让整个服务起不来


def save_secrets(payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = data_path("secrets")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def resolve_ai_settings():
    """算出当前生效的 LLM 配置（环境变量优先，secrets 文件兜底）。"""
    s = load_secrets()
    return {
        "key": os.environ.get("CASE_LIB_AI_KEY") or s.get("ai_key") or "",
        "base": (os.environ.get("CASE_LIB_AI_BASE") or s.get("ai_base") or "").rstrip("/"),
        "model": os.environ.get("CASE_LIB_AI_MODEL") or s.get("ai_model") or "",
    }


# 同一时刻只允许一个 AI 任务（候选池就几十条，串行足够，也好观察日志）
#
# items 是「这一轮审了哪些、各审成什么样」的逐条留档 —— 光有 log 和
# summary 的话，人只能看到「处理 1 条」这种总数，压根不知道审的是哪一条、
# 结果如何、下一步该点哪儿。items 就是给界面那张结果表用的。
_AI_JOB = {"state": "idle", "log": [], "summary": None, "items": [],
           "started_at": None, "finished_at": None}
_AI_JOB_LOCK = threading.Lock()
_AI_LOG_MAX = 400          # 日志最多留这么多行，防长跑撑爆内存


def _ai_log(line):
    with _AI_JOB_LOCK:
        _AI_JOB["log"].append("%s  %s" % (
            datetime.now().strftime("%H:%M:%S"), line))
        del _AI_JOB["log"][:-_AI_LOG_MAX]


def _ai_item(rec):
    """把一条核实结果收进 _AI_JOB['items']（界面结果表的数据源）。

    核完一条就落一条，不等整轮结束 —— 长轮次里人可以先看着已经出来的
    结果去操作，不用干等最后那条汇总。
    """
    with _AI_JOB_LOCK:
        items = _AI_JOB["items"]
        # 同一条重跑时覆盖旧的，别在表里堆出两行
        for i, old in enumerate(items):
            if old.get("id") == rec.get("id"):
                items[i] = rec
                return
        items.append(rec)


def _ai_run_job(opts):
    """后台线程：跑 AI 核实流水线。

    走 HTTP 调自己的 /api/candidates/... 接口存草稿与发布 —— 不抄近路
    直接改 json，这样规则引擎这道闸门必然经过，跟人工点发布没有区别。
    自己给自己铸管理员 cookie（拿的是内存里的签名密钥，不需要明文密码）。
    """
    try:
        st = resolve_ai_settings()
        AIV.configure(api_key=st["key"], base=st["base"], model=st["model"])
        if _AUTH_DISABLED:
            cookie = "case_admin=selftest"      # 自测关了鉴权，占位即可
        else:
            cookie = AUTH.cookie_header(AUTH.make_token(AUTH.get_secret(_ADMIN_CFG)))
        admin = AIV.Admin("http://127.0.0.1:%d" % ADMIN_PORT, cookie=cookie)

        cands = AIV.load_json("candidates")
        selected = AIV.pick_candidates(
            cands, limit=opts.get("limit"),
            ids=opts.get("ids") or None,
            include_small=opts.get("include_small"))
        if opts.get("all"):
            selected = AIV.pick_candidates(cands, limit=None, include_small=opts.get("include_small"))
        _ai_log("AI：%s @ %s｜本次 %d 条｜%s" % (
            AIV.AI_MODEL, AIV.AI_BASE or "（默认）", len(selected),
            "核完直接发布" if opts.get("publish") else "只存草稿"))

        done = published = failed = held = 0
        for c in selected:
            try:
                r = AIV.verify_one(c, admin,
                                   publish=opts.get("publish"),
                                   min_score=opts.get("min_score") or 0,
                                   log=_ai_log)
                done += 1
                if r.get("published"):
                    published += 1
                elif r.get("held"):
                    held += 1
                elif not r.get("ok", True):
                    failed += 1
            except Exception as e:
                failed += 1
                _ai_log("== %s  [失败] %s" % (c.get("id"), e))
                r = {"id": c.get("id"), "name": c.get("name"), "ok": False,
                     "error": str(e)}
            # 逐条留档：审的是哪条、审成什么样、还差什么（界面按它渲染结果表）
            r = dict(r or {})
            r.setdefault("id", c.get("id"))
            r.setdefault("name", c.get("name"))
            _ai_item(r)
        summary = {"done": done, "published": published,
                   "held": held, "failed": failed}
        _ai_log("—— 处理 %d 条｜已发布 %d｜分不够只存草稿 %d｜未成 %d" % (
            done, published, held, failed))
        with _AI_JOB_LOCK:
            _AI_JOB["summary"] = summary
            _AI_JOB["state"] = "done"
    except Exception as e:
        _ai_log("[任务出错] %s" % e)
        with _AI_JOB_LOCK:
            _AI_JOB["state"] = "error"
            _AI_JOB["summary"] = {"error": str(e)}
    finally:
        with _AI_JOB_LOCK:
            _AI_JOB["finished_at"] = datetime.now().strftime("%H:%M:%S")


def load_site():
    """站点元信息（站名、公众号、社群、仓库地址）。

    它不是对外数据契约的一部分，只是给前端和预渲染脚本读的展示配置，
    所以读不到就返回空字典，由调用方兜底——缺一个文件不该让整个接口 500。
    """
    path = data_path("site")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:                                    # noqa: BLE001
        sys.stderr.write("[case-lib] data/site.json 读不了：%s\n" % e)
        return {}


def load_verifications():
    """核实草稿：candidate_id -> 已填的核实配置。

    单独存一个文件而不是塞进 candidates.json，理由是草稿的写入频率远高于
    正式数据（勾一下存一下），混在一起会让 candidates.json 的 diff 全是噪音，
    也没法一眼看出「哪些条目被核实过」。
    """
    path = data_path("verifications")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:                                    # noqa: BLE001
        sys.stderr.write("[case-lib] data/verifications.json 读不了：%s\n" % e)
        return {}


def save_verifications(payload):
    save_json("verifications", payload)


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


def apply_human_read(case, cfg, draft=None):
    """把「人工核读」的状态写到案例上。

    为什么要有这一步：这个标记原先只活在草稿里，一提升成案例就什么都不剩 ——
    案例字段里既没有 human_read 也没有 musts，事后查不出「这条数字有没有人核过」。
    于是它成了纯摩擦：每次提升都要求人点一下，换来的承诺当场蒸发。

    两个来源各管一半，缺一不可：
      · **有没有勾** 以本次提交的 cfg 为准（那就是发布那一刻界面上的状态）；
      · **什么时候勾的** 取服务端存下的草稿（PUT /verification 在第一次勾上时铸的），
        不是本次提升的时刻 —— 用户可能是三天前勾的。
    请求体里的 human_read_at 一律不采信，否则前端能自己伪造成背书时间。
    """
    ticked = "human_read" in ((cfg or {}).get("musts") or [])
    at = str((draft or {}).get("human_read_at") or "") if ticked else ""
    if ticked and not at:
        # 没有存稿（比如手工建的案例）：按当前时刻兜底，仍然由服务端铸
        at = datetime.now().strftime("%Y-%m-%d %H:%M")
    case["human_read"] = bool(at)
    case["human_read_at"] = at
    return at


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


def build_payload(include_private=False):
    """组装 /api/data 的响应。

    include_private 控制「核实草稿」这类后台数据是否带出。未登录时不带：
    草稿里记的是人工核实过程中的判断和来源，属于内部作业内容，
    不该让任何能连上本机服务的人看到。
    """
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
        "premium": sum(1 for c in cases if c.get("tier") == VRULES.TIER_PREMIUM),
        "backup": sum(1 for c in cases if c.get("tier") == VRULES.TIER_BACKUP),
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
        # 对外数据契约的版本号。改字段名/类型/枚举要在这里 +1，并同步 docs/DATA_SCHEMA.md；
        # 只是加新字段不用动。见 docs/DATA_SCHEMA.md「Stability and versioning」。
        "schema_version": SCHEMA_VERSION,
        # 正文字段（one_liner / verdict / note …）的语言。结构化字段（分数、枚举、
        # 数值、日期、URL）与语言无关，第三方程序可以直接消费。
        "lang": CONTENT_LANG,
        "cases": cases,
        "candidates": candidates,
        "inbox": inbox,
        "sources": sources,
        # 展示配置（公众号 / 社群 / 仓库）。前端用它渲染引流位，别在页面里写死。
        "site": load_site(),
        # 核实草稿（候选 id -> 已填条件）。界面打开工作台时直接读它回填。
        # 仅管理员可见；游客拿到空字典，界面据此不渲染后台入口。
        "verifications": load_verifications() if include_private else {},
        "stats": stats,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


class Handler(BaseHTTPRequestHandler):
    """公开站：只读。

    两个站共用这一份实现，靠 SITE / DOC_DIR 两个类变量分开：
      SITE     —— 决定哪些接口存在（后台接口在公开站上一律 404）
      DOC_DIR  —— 决定静态文件从哪个目录取（static/ 还是 admin/）

    子类 AdminHandler 只改这两个变量，其他逻辑一行都不用重复。
    """
    SITE = "public"
    DOC_DIR = STATIC_DIR
    server_version = "CaseLibrary/1.0"

    def log_message(self, fmt, *args):  # 静音默认日志
        if os.environ.get("CASE_LIB_VERBOSE"):
            sys.stderr.write("[case-lib] " + (fmt % args) + "\n")

    # ---------- 站点判定 ----------
    @property
    def is_admin_site(self):
        return self.SITE == "admin"

    def _not_on_public(self):
        """后台接口出现在公开站上时的统一回应。

        用 404 而不是 401/403：401 等于在告诉对方「这儿有东西，只是你没权限」，
        而 404 和请求一张不存在的图片没有区别 —— 别人扫不出来这儿有个后台。
        """
        return self._json({"error": "not found"}, 404)

    # ---------- 工具 ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8",
              extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, val in (extra_headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, obj, code=200, extra_headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8", extra_headers)

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

    # ---------- 管理员身份 ----------
    def _cookie_token(self):
        """从 Cookie 头里取出 token。手解析而不是用 http.cookies，
        只是为了避免不规范 cookie 把解析抛异常，这里宁可返回 None。"""
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith(AUTH.COOKIE_NAME + "="):
                return part.split("=", 1)[1].strip()
        return None

    def is_admin(self):
        if _AUTH_DISABLED:
            return True
        token = self._cookie_token()
        if not token or token in _REVOKED:
            return False
        return AUTH.check_token(AUTH.get_secret(_ADMIN_CFG), token)

    def _revoke(self, token):
        """登出：让这个 token 立刻失效，并顺手清掉名单里已过期的。"""
        if not token:
            return
        _REVOKED.add(token)
        now = int(time.time())
        for old in list(_REVOKED):
            head = old.split(".", 1)[0]
            if head.isdigit() and int(head) < now:
                _REVOKED.discard(old)

    def _client_ip(self):
        return self.client_address[0] if self.client_address else "?"

    def _login_locked_for(self):
        """还要锁多少秒。0 表示没锁。"""
        rec = _LOGIN_FAILS.get(self._client_ip())
        if not rec:
            return 0
        fails, first = rec
        now = time.time()
        if now - first > LOGIN_FAIL_WINDOW:
            _LOGIN_FAILS.pop(self._client_ip(), None)     # 窗口过了，既往不咎
            return 0
        if fails < LOGIN_MAX_FAILS:
            return 0
        left = int(LOGIN_LOCK_SECONDS - (now - first))
        return max(1, left)

    def _require_admin(self):
        """写接口的统一闸门。挡不住就返回 401，调用方直接 return 它的结果。"""
        if self.is_admin():
            return None
        return self._json({
            "error": "需要管理员登录",
            "hint": "核实与入库是后台操作，请先登录",
            "admin": False,
        }, 401)

    def _login(self):
        """校验密码并下发 HttpOnly cookie。"""
        ip = self._client_ip()
        locked = self._login_locked_for()
        if locked:
            return self._json({
                "error": "密码连续输错太多次，请 %d 秒后再试" % locked,
                "admin": False,
            }, 429)

        body = self._read_body()
        password = str(body.get("password") or "")
        cfg = _ADMIN_CFG or AUTH.load_admin() or {}

        if not AUTH.verify_password(password, cfg.get("password_hash", "")):
            fails, first = _LOGIN_FAILS.get(ip, (0, time.time()))
            _LOGIN_FAILS[ip] = (fails + 1, first)
            left = max(0, LOGIN_MAX_FAILS - (fails + 1))
            # 失败时多耗一拍。PBKDF2 本身已经很慢，再补这个延迟是让
            # 本地起脚本撞库变得不划算。
            time.sleep(0.5)
            return self._json({
                "error": "密码不对" + ("（再错 %d 次将锁定 5 分钟）" % left if left else "（已锁定）"),
                "admin": False,
            }, 401)

        _LOGIN_FAILS.pop(ip, None)          # 登成功就清账
        token = AUTH.make_token(AUTH.get_secret(cfg))
        return self._json(
            {"ok": True, "admin": True},
            extra_headers={"Set-Cookie": AUTH.cookie_header(token)})

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path

        # 登录状态：后台前端靠它决定显示登录框还是后台主体。
        # 注意这里不能用缓存 —— 退出登录后要立刻反映到界面上。
        if path == "/api/session":
            return self._json({
                "admin": self.is_admin() if self.is_admin_site else False,
                "site": self.SITE,
                # 静态版没有后端，这个接口根本不会存在，前端据此判定为游客
                "static": False,
            })

        if path == "/api/data":
            # 公开站永远只给公开数据，哪怕浏览器带着后台的 cookie 也一样 ——
            # cookie 是不认端口的，登录后台后再打开公开站，is_admin() 仍为 True，
            # 所以这里必须再拿站点判定挡一道，否则核实草稿就从公开站漏出去了。
            private = self.is_admin() and self.is_admin_site
            return self._json(build_payload(include_private=private))
        if path == "/api/stats":
            private = self.is_admin() and self.is_admin_site
            return self._json(build_payload(include_private=private)["stats"])
        if path == "/api/ping":
            return self._json({
                "ok": True,
                "site": self.SITE,
                "port": ADMIN_PORT if self.is_admin_site else PORT,
            })

        # 下面的接口只在后台端口存在。公开站上请求一律 404，
        # 连「这里有个接口只是你没权限」都不透露。
        if path.startswith("/api/"):
            if not self.is_admin_site:
                return self._not_on_public()

        # 核实规则表：界面按它渲染工作台，规则改了界面自动跟着变。
        # 规则本身不算机密，但仍要登录才给 —— 它是后台界面的一部分。
        if path == "/api/verify-schema":
            denied = self._require_admin()
            if denied:
                return denied
            return self._json(VRULES.schema())

        # ---- AI 自动核实（后台「AI 核实」面板打到这几个接口）----
        if path == "/api/ai/plan":
            denied = self._require_admin()
            if denied:
                return denied
            # 离线：读候选池 + 规则引擎算卡点，不联网不改动
            return self._json(AIV.plan_data())

        if path == "/api/ai/status":
            denied = self._require_admin()
            if denied:
                return denied
            with _AI_JOB_LOCK:
                job = {k: (list(v) if isinstance(v, list) else v)
                       for k, v in _AI_JOB.items()}
                # items 里是嵌套 dict，浅拷贝会被后面的写入影响，逐条深拷一层
                job["items"] = [dict(x) for x in _AI_JOB.get("items") or []]
            st = resolve_ai_settings()
            return self._json({
                "job": job,
                "has_key": bool(st["key"]),
                "model": st["model"] or AIV.AI_MODEL,
                "base": st["base"] or AIV.AI_BASE,
            })

        # 某条候选的核实草稿 + 实时判定结果（后台数据，必须登录）
        m = re.match(r"^/api/candidates/([^/]+)/verification$", path)
        if m:
            denied = self._require_admin()
            if denied:
                return denied
            cid = unquote(m.group(1))
            draft = load_verifications().get(cid) or VRULES.blank()
            return self._json({
                "candidate_id": cid,
                "draft": draft,
                "result": VRULES.evaluate(draft),
            })

        # 静态文件：公开站取 static/，后台取 admin/，两边互不可见 ——
        # 在公开站上请求 /app.js 拿不到后台的脚本，因为目录压根不一样。
        if path in ("/", ""):
            path = "/index.html"
        rel = unquote(path).lstrip("/").replace("..", "")
        full = os.path.normpath(os.path.join(self.DOC_DIR, rel))
        if not full.startswith(os.path.normpath(self.DOC_DIR)):
            return self._send(403, "forbidden", "text/plain; charset=utf-8")
        if not os.path.isfile(full):
            return self._send(404, "not found", "text/plain; charset=utf-8")

        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    def do_PUT(self):
        path = urlparse(self.path).path

        if not self.is_admin_site:      # 公开站没有写接口
            return self._not_on_public()
        denied = self._require_admin()
        if denied:
            return denied

        # 存核实草稿（随时可中断，下次打开接着填）
        m = re.match(r"^/api/candidates/([^/]+)/verification$", path)
        if not m:
            return self._json({"error": "unknown route"}, 404)

        cid = unquote(m.group(1))
        body = self._read_body()
        with _LOCK:
            candidates = load_json("candidates")
            if not any(c.get("id") == cid for c in candidates):
                return self._json({"error": "candidate not found: " + cid}, 404)

            drafts = load_verifications()
            # 只收白名单字段，别把前端传来的任意键写进数据文件。
            # gates_denied 必须在白名单里：上一版漏了它，AI 抓到的明确反证
            # 存草稿时被静默丢掉，三态门槛等于只落地了「通过」那一态。
            _gate_keys = {g["key"] for g in VRULES.GATES}
            musts = [str(x) for x in (body.get("musts") or [])]
            # human_read_at 由服务端铸，**不接受请求体里的值** —— 否则前端可以
            # 自己填一个背书时间。语义是「第一次勾上『我亲自看过原文』的时刻」：
            # 勾上时记时间，取消勾选时清掉，之后再勾会重新计时。
            prev = drafts.get(cid) or {}
            hr_at = str(prev.get("human_read_at") or "")
            if "human_read" in musts:
                hr_at = hr_at or datetime.now().strftime("%Y-%m-%d %H:%M")
            else:
                hr_at = ""
            draft = {
                "gates": [str(x) for x in (body.get("gates") or [])],
                "gates_denied": [str(x) for x in (body.get("gates_denied") or [])
                                 if str(x) in _gate_keys],
                "musts": musts,
                "bonus": [str(x) for x in (body.get("bonus") or [])],
                "caliber": str(body.get("caliber") or ""),
                # AI 对口径的判断理由 + 它这一轮对「口径与数字一致」的原始回答。
                # 以前这两个都不存，于是后台只剩「还差 N 项必填」，看不出 AI 是
                # 「没找到证据」还是「找到了反证」。漏进白名单就会被静默丢掉
                # —— 本项目为此踩过 gates_denied 那次。
                "caliber_reason": str(body.get("caliber_reason") or "")[:1200],
                "caliber_consistent_ai": (body.get("caliber_consistent_ai")
                                          if isinstance(body.get("caliber_consistent_ai"), bool)
                                          else None),
                "verification": str(body.get("verification") or ""),
                "source_kinds": [str(x) for x in (body.get("source_kinds") or [])],
                "sources": body.get("sources") or [],
                # 被来源黑名单挡掉的条数（词典/音乐/电商等）。漏进白名单会被静默丢掉，
                # 后台就看不到「这次 AI 又去查词典了」这个信号。
                "irrelevant_sources_dropped": int(body.get("irrelevant_sources_dropped") or 0),
                "corrections": body.get("corrections") or [],
                "note": str(body.get("note") or ""),
                "human_read_at": hr_at,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            drafts[cid] = draft
            save_verifications(drafts)
        return self._json({"ok": True, "draft": draft, "result": VRULES.evaluate(draft)})

    def do_PATCH(self):
        path = urlparse(self.path).path

        if not self.is_admin_site:
            return self._not_on_public()
        denied = self._require_admin()
        if denied:
            return denied

        # 备选池 -> 精品池：补完条件后拔档，不用重新发布一条
        m = re.match(r"^/api/cases/([^/]+)/tier$", path)
        if m:
            cid = unquote(m.group(1))
            body = self._read_body()
            cfg = body.get("verification_config") or body
            result = VRULES.evaluate(cfg)
            if not result["publishable"]:
                return self._json({
                    "error": "条件不齐，不能拔档到精品池",
                    "result": result,
                }, 409)
            with _LOCK:
                cases = load_json("cases")
                for c in cases:
                    if c.get("id") == cid:
                        # 同一套定档政策（见 verify_rules.default_case_tier）：
                        # 有一手来源就默认精品，质量分只是其次。
                        tier, tier_reason = VRULES.default_case_tier({
                            "source_kinds": cfg.get("source_kinds") or [],
                            "sources": cfg.get("sources") or [],
                            "quality_score": result["bonus_score"],
                        })
                        c["tier"] = tier
                        c["tier_reason"] = tier_reason
                        c["quality_score"] = result["bonus_score"]
                        if cfg.get("verification"):
                            c["verification"] = cfg["verification"]
                        if cfg.get("caliber"):
                            c["caliber"] = cfg["caliber"]
                        if cfg.get("sources"):
                            c["sources"] = cfg["sources"]
                        if cfg.get("corrections"):
                            c["corrections"] = cfg["corrections"]
                        # 人工核读状态跟着刷新：优先用源候选草稿里服务端铸的时间戳
                        apply_human_read(c, cfg,
                                         load_verifications().get(c.get("published_from") or ""))
                        c["updated_at"] = datetime.now().strftime("%Y-%m-%d")
                        save_json("cases", cases)
                        return self._json({"ok": True, "case": c, "result": result})
            return self._json({"error": "case not found: " + cid}, 404)

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
                        # 人工核读的两个字段不接受这里的任意写入 —— 它们要能回答
                        # 「有没有人真的核过」，所以只能经 PUT /verification（勾选）
                        # 或 apply_human_read（服务端铸时间戳）落地。这个路由本来就是
                        # 任意键直写，不挡一下的话前端能给自己盖一个背书戳。
                        if k in ("human_read", "human_read_at"):
                            continue
                        c[k] = v
                    c["updated_at"] = datetime.now().strftime("%Y-%m-%d")
                    save_json("cases", cases)
                    return self._json({"ok": True, "case": c})
        return self._json({"error": "case not found: " + cid}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        # 公开站连登录接口都没有 —— 撞库总得有个能撞的门。
        if not self.is_admin_site:
            return self._not_on_public()

        # 登录 / 登出是唯一两个不需要管理员身份的写接口 ——
        # 否则没人能拿到身份，就死锁了。
        if path == "/api/login":
            return self._login()
        if path == "/api/logout":
            # 先吊销再清 cookie：只清 cookie 的话，旧 token 被人留着照样能用
            self._revoke(self._cookie_token())
            return self._json({"ok": True, "admin": False},
                              extra_headers={"Set-Cookie": AUTH.clear_cookie_header()})

        denied = self._require_admin()
        if denied:
            return denied

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

                # 核实条件：优先用请求体里带来的，没带就读已存的草稿。
                # 发布前必须过规则引擎 —— 这是这个库唯一不可绕过的闸门。
                draft = body.get("verification_config")
                if draft is None:
                    draft = load_verifications().get(cid) or VRULES.blank()
                result = VRULES.evaluate(draft)

                if not result["publishable"]:
                    if result["gates_failed"]:
                        return self._json({
                            "error": "门槛未过，不能入库：" +
                                     "；".join(g["label"] for g in result["gates_failed"]),
                            "result": result,
                        }, 409)
                    return self._json({
                        "error": "还差 %d 项必填：%s" % (
                            result["missing_count"],
                            "；".join(m["label"] for m in result["missing"])),
                        "result": result,
                    }, 409)

                taken = {c.get("id") for c in cases}
                now = datetime.now().strftime("%Y-%m-%d")

                # 核实等级不能高于来源证据 —— 入库时就兜一道，免得又攒出一批虚标。
                # 曾经 11 条案例标着 stripe / official，来源却只有第三方拆解站：
                # 那种标注是替读者做了一次他们没授权的信任背书。
                # 判定规则只在 verify_rules 里写了一遍，这里只是调用。
                # 降了就留下原值和理由，事后查得到，也改得回去。
                claim_ver = (draft.get("verification")
                             or hit.get("verification", "unverified"))
                claim_kinds = [s.get("kind") for s in (draft.get("sources") or [])
                               if isinstance(s, dict) and s.get("kind")]
                if not claim_kinds:
                    claim_kinds = draft.get("source_kinds") or []
                dn_to, dn_why = VRULES.evidence_gap(claim_ver, claim_kinds)

                # 档位不走 result["tier"]，而走 default_case_tier：
                # 来源里有一手证据（stripe/official）就默认进精品池，其次才看质量分。
                # 质量分照旧存下来 —— 后台会显示成「精品 · 35 分」，
                # 让「数字可信」和「核实做得多全」两件事都看得见。
                # 老实现只按质量分定档，于是「来源极硬、还没补 playbook」的案例
                # 全被压进备选池，精品池长期只剩一两条。
                new_tier, tier_reason = VRULES.default_case_tier({
                    "source_kinds": draft.get("source_kinds") or [],
                    "sources": draft.get("sources") or [],
                    "quality_score": result["bonus_score"],
                })

                # 人工核读标记在 new_case 建好后再写（见下方 apply_human_read）：
                # 时间戳一律取**服务端存下的草稿**，不用请求体里的。
                new_case = {
                    "id": slugify(hit.get("name_en") or hit.get("name"), taken),
                    "name": hit.get("name"),
                    "name_en": hit.get("name_en"),
                    "origin": hit.get("origin", ""),
                    "one_liner": hit.get("one_liner", ""),
                    "category": hit.get("category", "未分类"),
                    "industry": hit.get("industry", ""),
                    "status": "curated",
                    # 核实等级以草稿为准 —— 它才是人工判定过的那个。
                    # 但草稿标得太高时，降到证据撑得住的那一档（dn_to），
                    # 并在下方写 verification_downgraded 记下原值与理由。
                    "verification": dn_to or claim_ver,
                    "caliber": draft.get("caliber", ""),
                    "metrics": hit.get("metrics", {}),
                    "models": hit.get("models", []),
                    "replicability": hit.get("replicability", {}),
                    "verdict": hit.get("verdict", ""),
                    "what_it_does": hit.get("what_it_does") or hit.get("one_liner", ""),
                    "how_it_makes_money": hit.get("how_it_makes_money", ""),
                    "why_it_works": hit.get("why_it_works", []),
                    "playbook": hit.get("playbook", []),
                    "signals": hit.get("signals", []),
                    # 来源与修正优先用草稿里登记的，没有才回落到候选自带的
                    "corrections": draft.get("corrections") or hit.get("corrections", []),
                    "sources": draft.get("sources") or hit.get("sources", []),
                    "published_from": cid,
                    "verified_at": now,
                    "updated_at": now,
                    "tags": hit.get("tags", []),
                    # 两档：有一手来源默认精品，否则看质量分（政策见
                    # verify_rules.default_case_tier）。备选不是废品，
                    # 是「先入库留着，条件补齐了随时拔档」。
                    "tier": new_tier,
                    "tier_reason": tier_reason,
                    "quality_score": result["bonus_score"],
                    # 发布时拦发布的必填项已齐全，所以不需要再挂待核实标记。
                    # 「人工核读」是标记不是闸门，它的落盘见下方 apply_human_read。
                    "needs_review": False,
                }
                if body.get("fields"):
                    new_case.update({k: v for k, v in body["fields"].items() if k != "id"})
                # 放在 fields 覆盖之后：核读状态只能由服务端定，body 里带什么都不算。
                # 时间戳取自 PUT /verification 存下的草稿 —— 那是用户真正点勾的时刻，
                # 不是这次提升的时刻。案例从此能回答「这条数字有没有人核过」。
                apply_human_read(new_case, draft, load_verifications().get(cid))
                # 同样放在 fields 覆盖之后：等级降没降只有服务端说了算。
                if dn_to:
                    new_case["verification_downgraded"] = {
                        "from": claim_ver,
                        "to": dn_to,
                        "reason": dn_why,
                        "at": now,
                        "tool": "promote",
                    }
                cases.insert(0, new_case)
                save_json("cases", cases)
                save_json("candidates", rest)

                # 草稿用完即清，避免下次打开看到的是上一条的残留
                drafts = load_verifications()
                if cid in drafts:
                    drafts.pop(cid)
                    save_verifications(drafts)

                return self._json({"ok": True, "case": new_case, "result": result}, 201)

        # ---- AI 自动核实：保存 LLM 配置 / 启动流水线任务 ----
        if path == "/api/ai/settings":
            body = self._read_body()
            with _LOCK:
                s = load_secrets()
                key = body.get("api_key")
                if key is not None:
                    key = str(key).strip()
                    if key:
                        s["ai_key"] = key
                        # 立刻在本进程生效（GUI 保存后不用重启）
                        os.environ["CASE_LIB_AI_KEY"] = key
                    else:
                        s.pop("ai_key", None)          # 清空 = 删除
                        os.environ.pop("CASE_LIB_AI_KEY", None)
                if body.get("ai_base") is not None:
                    b = str(body["ai_base"]).strip().rstrip("/")
                    if b and not b.startswith("http"):
                        return self._json({"error": "接口地址要以 http(s):// 开头"}, 400)
                    s["ai_base"] = b
                if body.get("ai_model") is not None:
                    s["ai_model"] = str(body["ai_model"]).strip()
                save_secrets(s)
            st = resolve_ai_settings()
            AIV.configure(api_key=st["key"], base=st["base"], model=st["model"])
            return self._json({"ok": True, "has_key": bool(st["key"]),
                               "model": st["model"] or AIV.AI_MODEL})

        if path == "/api/ai/run":
            with _AI_JOB_LOCK:
                if _AI_JOB["state"] == "running":
                    return self._json({"error": "已有任务在跑，等它结束再开"}, 409)
            body = self._read_body()
            if not resolve_ai_settings()["key"]:
                return self._json({
                    "error": "还没有配置 LLM API Key —— 先在下方保存，"
                             "或启动前设 CASE_LIB_AI_KEY 环境变量"}, 400)
            try:
                limit = int(body.get("limit") or 5)
                min_score = int(body.get("min_score") or 0)
            except (TypeError, ValueError):
                return self._json({"error": "limit / min_score 要是数字"}, 400)
            opts = {
                "limit": max(1, min(limit, 50)),
                "all": bool(body.get("all")),
                "ids": [str(i) for i in (body.get("ids") or [])][:50],
                "include_small": bool(body.get("include_small")),
                "publish": bool(body.get("publish")),
                "min_score": max(0, min(min_score, 100)),
            }
            with _AI_JOB_LOCK:
                _AI_JOB.update({"state": "running", "log": [], "summary": None,
                                "items": [],
                                "started_at": datetime.now().strftime("%H:%M:%S"),
                                "finished_at": None})
            threading.Thread(target=_ai_run_job, args=(opts,), daemon=True).start()
            return self._json({"ok": True, "state": "running"})

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
                # 手动新增的案例没有核实草稿，按备选对待；
                # 但已经填了完整核实条件的，就按规则正常评档。
                cfg = body.pop("verification_config", None)
                if cfg is not None:
                    result = VRULES.evaluate(cfg)
                    if not result["publishable"]:
                        return self._json({
                            "error": "核实条件不齐全，不能入库",
                            "result": result,
                        }, 409)
                    # 与提升路径同一套定档政策，别让两个入口算出不同档位
                    tier, tier_reason = VRULES.default_case_tier({
                        "source_kinds": cfg.get("source_kinds") or [],
                        "sources": cfg.get("sources") or [],
                        "quality_score": result["bonus_score"],
                    })
                    body["tier"] = tier
                    body["tier_reason"] = tier_reason
                    body["quality_score"] = result["bonus_score"]
                    body["verification"] = cfg.get("verification") or body["verification"]
                    body["caliber"] = cfg.get("caliber", "")
                    body["needs_review"] = False
                else:
                    body.setdefault("tier", VRULES.TIER_BACKUP)
                    body.setdefault("tier_reason", "手动新增、未附核实条件")
                    body.setdefault("quality_score", 0)
                body.setdefault("created_at", datetime.now().strftime("%Y-%m-%d"))
                body["updated_at"] = datetime.now().strftime("%Y-%m-%d")
                # 与其它两条入口一致：核读状态由服务端定，body 里带的不可信。
                # 这条路径没有候选草稿可查，所以只能按当前时刻兜底（仍是服务端铸）。
                apply_human_read(body, cfg)
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


class AdminHandler(Handler):
    """后台站：同一个进程里的第二个端口，界面在 admin/，写接口全在这里。"""
    SITE = "admin"
    DOC_DIR = ADMIN_DIR


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

    # AI 核实：启动时把 secrets 文件里的 LLM 配置装进流水线模块
    # （环境变量优先，所以这里不会覆盖外部显式传入的值）。
    st = resolve_ai_settings()
    AIV.configure(api_key=st["key"], base=st["base"], model=st["model"])
    if st["key"]:
        print("  AI 核实：已就绪（%s @ %s）——后台「AI 核实」面板可用" % (
            st["model"] or AIV.AI_MODEL, st["base"] or AIV.AI_BASE))
    else:
        print("  AI 核实：未配置 API Key —— 后台「AI 核实」面板里保存即可启用")

    # 管理员凭据：核实功能是后台功能，没登录就用不了。
    # 自测要绕过鉴权，所以提供 CASE_LIB_NO_AUTH —— 只有测试会设它。
    global _ADMIN_CFG, _AUTH_DISABLED
    _AUTH_DISABLED = os.environ.get("CASE_LIB_NO_AUTH") == "1"
    if _AUTH_DISABLED:
        print("[!] 已按 CASE_LIB_NO_AUTH=1 关闭鉴权（仅限自测，别在公网用）")
        _ADMIN_CFG = AUTH.load_admin() or {}
    else:
        _ADMIN_CFG, plain = AUTH.ensure_admin(os.environ.get(AUTH.ENV_PASSWORD))
        if plain:
            print("=" * 56)
            print("  已生成管理员密码（只显示这一次）：%s" % plain)
            print("  凭据存于 data/admin.json，想改就跑：")
            print("    python scripts/auth.py --reset")
            print("=" * 56)

    url = "http://127.0.0.1:%d/" % PORT
    admin_url = "http://127.0.0.1:%d/" % ADMIN_PORT

    # 两个站都只绑 127.0.0.1。改成 0.0.0.0 等于把核实和入库的接口
    # 放到局域网上，谁都能改你的库 —— 真要远程管，走 SSH 隧道。
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    admin_httpd = ThreadingHTTPServer(("127.0.0.1", ADMIN_PORT), AdminHandler)
    threading.Thread(target=admin_httpd.serve_forever, daemon=True).start()

    print("=" * 60)
    print("  拆解海外 · 已启动")
    print("")
    print("  公开站（只读）  %s" % url)
    print("  管理后台        %s" % admin_url)
    print("")
    print("  数据：%s" % DATA_DIR)
    if not _AUTH_DISABLED and not plain:
        # 密码只在第一次生成时显示一次；之后每次启动都提醒一句，
        # 免得有人拿着上一次会话打印的旧密码怀疑「是不是服务坏了」。
        print("  后台密码：沿用 data/admin.json 里已存的那份（启动不重复显示）。")
        print("  忘了就重设：python scripts/auth.py --reset")
    print("  后台是独立站点 —— 公开站上没有核实入口，也没有任何写接口。")
    if _AUTH_DISABLED:
        print("  鉴权：已关闭（CASE_LIB_NO_AUTH=1，仅限自测）")
    print("  Ctrl+C 停止")
    print("=" * 60)

    if not os.environ.get("CASE_LIB_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        admin_httpd.shutdown()
        admin_httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
