# -*- coding: utf-8 -*-
"""读 free-llm-probe 的档案/接口，把「现在能用的 LLM」接进核验流程。

**为什么需要这个**：`ai_verify.py` 原本只能手填 base/model/key 三个字段，
换模型要改环境变量或改代码。而「哪些模型现在真能调」是**会变的**——
同一个模型今天免费、下周可能下线。free-llm-probe 负责回答这个动态问题，
本模块负责把答案喂进核验流程。

三种来源（可组合，优先级从高到低）
------------------------------------
1. **单个档案 id**：`--llm-profile opencode-zen/space-bunny-free`
   从档案文件或 HTTP 接口里精确取一条。
2. **档案 + 自动挑选**：`--llm-profile out/profiles.json`
   文件里若有多条可用，取第一条（档案已按「可用优先」排序）。
3. **HTTP 接口**：`--llm-profile http://127.0.0.1:8787/usable`
   走 JSON 接口拿实时状态（消费方崩溃后自愈，不必等下次定时刷新）。

档案 schema 由 free-llm-probe 定义，本模块只读不改：
    {id, source, base, model, auth, no_key, ok, reason, freshness, last_ok_at, ...}

⚠ **要同时看 `ok` 和 `freshness`**。不可用档案（限地区/锁客户端/已下线）
留着只是给人看的，喂给核验流程只会浪费一轮抓取。
⚠ **`ok=true` 不等于现在能用**：free-llm-probe v2.0 起档案带五档时效，
`ok=true` + `freshness=expired` 表示「上次能用、现已连挂或超 7 天没成功」，
拿它调核验会一路失败。
"""
import json
import os
import re
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _fetch_json(url, timeout=20):
    """拉 JSON。走浏览器 UA，避免被 CDN/WAF 拦。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    # 本机代理对 localhost 无意义，但对外部可能有用，这里不强行绕过
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_payload(src):
    """从「档案文件路径」或「http(s) URL」读原始档案。

    返回 dict：{generated_at, version, profiles:[...]}
    """
    if src.startswith("http://") or src.startswith("https://"):
        return _fetch_json(src)
    path = os.path.abspath(os.path.expanduser(src))
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "找不到档案文件：%s\n"
            "先跑一次探测产出档案：\n"
            "  python free-llm-probe/probe.py "
            "--emit-profiles %s" % (path, src))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# 过期判定：ok=true 只说明「末次探测成功」，freshness=expired 说明
# 连挂≥3 次或超 7 天没成功 —— 实测已不可用却仍标 ok=true。
# 消费方只判 ok 会挑中这种条目，然后每次核验都失败。
EXPIRED = ("expired",)


def _is_usable(p):
    return (isinstance(p, dict) and p.get("ok")
            and (p.get("freshness") or "unknown") not in EXPIRED)


def pick(payload, want_id=None, allow_expired=False):
    """从档案里挑一条可用档案。

    want_id 形如 `opencode-zen/space-bunny-free`（也允许只写模型名）。

    ⚠ **必须同时看 `ok` 和 `freshness`**。free-llm-probe v2.0 起档案带
    五档时效；`ok=true` + `freshness=expired` 的条目已经连挂或超期，
    直接拿来调会失败。`allow_expired=True` 才放行（明确知道要重试时用）。
    """
    profiles = [p for p in (payload.get("profiles") or [])
                if isinstance(p, dict) and p.get("ok")
                and (allow_expired
                     or (p.get("freshness") or "unknown") not in EXPIRED)]
    if not profiles:
        # 单独诊断：到底是全挂了，还是有但过期了 —— 两者处置完全不同。
        allok = [p for p in (payload.get("profiles") or [])
                 if isinstance(p, dict) and p.get("ok")]
        if allok:
            raise RuntimeError(
                "档案里所有 ok=true 的条目都已过期（连挂≥3 次或超 7 天没成功）：\n"
                "  %s\n"
                "先重跑探测刷新档案：python -X utf8 probe.py --emit-profiles"
                % "\n  ".join(
                    "%s（%s）" % (p.get("id"), p.get("freshness"))
                    for p in allok[:10]))
        raise RuntimeError(
            "档案里没有可用档案（ok=true 的条目为 0）。\n"
            "可能原因：①真key 过期 ②限地区 ③上游全挂。\n"
            "用 --key 重新探测，或去 https://opencode.ai/zen 拿 key。")
    if not want_id:
        return profiles[0]          # 档案已按可用优先排序
    # 精确匹配 id
    for p in profiles:
        if p.get("id") == want_id:
            return p
    # 只写模型名也能匹配（但多个源同名字段要挑明确的）
    hits = [p for p in profiles
            if p.get("model") == want_id or p.get("id", "").endswith(
                "/" + want_id)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise RuntimeError(
            "模型名 %r 匹配到多个档案，请用完整 id：\n  %s"
            % (want_id, "\n  ".join(h["id"] for h in hits)))
    # 退一步：允许前缀模糊（用户可能记不全）
    cand = [p for p in profiles if want_id in p.get("id", "")]
    if len(cand) == 1:
        return cand[0]
    raise RuntimeError("档案里没有 %r，可用的是：\n  %s" % (
        want_id, "\n  ".join(p["id"] for p in profiles)))


def describe(p):
    """给命令行回显用的一行摘要。**带上时效** —— 不标时效等于误导。"""
    fresh = p.get("freshness") or "unknown"
    if fresh in EXPIRED:
        return "%s（%s，%s）⚠ 已过期：%s" % (
            p.get("id"), p.get("base"), p.get("model"),
            p.get("last_ok_at") or "从未成功过")
    return "%s（%s，%s，%s）" % (
        p.get("id"), p.get("base"), p.get("model"), fresh)


def resolve(spec):
    """`--llm-profile` 的值 → 档案 dict。

    spec 允许：
      · 档案文件路径        out/profiles.json
      · HTTP URL      http://127.0.0.1:8787/usable
      · 档案文件 + 冒号 + id   out/profiles.json:opencode-zen/space-bunny-free
    """
    want_id = None
    # ⚠ URL 里也有冒号（http://），所以只在**有 payload 后缀**时才从右切
    if "://" not in spec and ":" in spec:
        head, tail = spec.rsplit(":", 1)
        if head and tail and ("/" in head or tail.count(".") == 0):
            spec, want_id = head, tail
    payload = load_payload(spec)
    return pick(payload, want_id)


def to_llm_args(p):
    """档案 → (base, model, key) 三元组，直接喂给 ai_verify.configure()。

    ⚠ 档案里 `auth` 为空表示「免 key」，这里返回空串而不是 None ——
    ai_verify 靠 `AI_KEY or ""` 走空 Bearer 分支（见其 llm() 注释）。
    """
    return (p.get("base") or "", p.get("model") or "",
            p.get("auth") or "")


def list_usable(payload):
    """列出所有可用档案 id，供报错时提示。与 pick() 口径保持一致。"""
    return [p.get("id") for p in (payload.get("profiles") or []) if _is_usable(p)]
