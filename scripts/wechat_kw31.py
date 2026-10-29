#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat_kw31.py — 每天自动取公众号「消息分析 → 消息关键词」里指定关键词的统计
==========================================================================

背景
----
「万物解释者」推文（31 个可复制的 AI 产品）的转化路径是：读者在公众号对话框
发消息「31」→ 关键词自动回复返回微信号 + 收款码。要衡量这条路径，就得每天
跟踪「消息分析 → 消息关键词」里「31」这个词的统计。本脚本把这件事自动化。

口径（2026-10-23 实测抠出，非猜的）
----------------------------------
* 页面路径：数据分析 → 消息分析 → 「消息关键词」Tab
  `/misc/messageanalysis?action=keyword&t=statistics/msg_keyword&type=-1`
  （Tab 定义在 `statistics/msg_fe.*.js` 的 msg_top.js 里；`type=daily` 那个
  URL 是「消息分析」主 Tab，不是关键词）
* 数据接口（在 mp 页面里 fetch，自动带 cookie + token）:
  `GET /misc/messageanalysis?type=-1&action=keyword
       &begin_date=YYYY-MM-DD&end_date=YYYY-MM-DD
       &token=<运行时动态>&lang=zh_CN&f=json&ajax=1`
  返回 `{base_resp:{ret:0}, list:[{keyword, exp_count}, ...]}`，
  list 按出现次数降序（页面前端只在本地分页/搜索）。
* 表格四列：**排行 | 消息关键词 | 出现次数 | 占比**
  ⇒ `exp_count` = **出现次数**（区间内含该关键词的消息条数，**不是人数**，
     平台这个视图不给人数）。占比 = 该词次数 / 全部关键词次数之和。
* ⚠⚠ **平台滤除口径**：页面明写「出于性能考虑，在每日数据中滤除了出现次数
  小于 2 次的关键词」。所以**单日**查不到「31」时，只能记「当日 0 或 1 次」，
  无法区分 —— 台账里 present=false / exp_count=null 就是这个含义，
  **不要**当成 0 写死。
* 因此每日同时取一个 **7 日滚动口径**（end_date 往前 6 天）作平滑参照：
  多天累加后 <2 的滤除影响小得多，趋势更可信。两个口径分开记，别混。

台账
----
`data/wechat_kw31_daily.json`，按日期幂等（同一天重跑覆盖当天，不动历史）::

    {"2026-10-22": {"keyword": "31",
                    "day":   {"present": true,  "exp_count": 3, "rank": 2,
                              "total_keywords": 42},
                    "rolling7": {"present": true, "exp_count": 11, "rank": 1,
                                 "total_keywords": 87},
                    "fetched_at": "2026-10-23T01:30:00+08:00"}}

自检：每次运行都会拿页面**服务端内嵌**的 30 日列表（`window.cgiData.keyword_list`，
即用户在浏览器里看到的那个默认视图）与接口同区间结果对账，不一致只告警不中断。

用法
----
    python -X utf8 scripts/wechat_kw31.py                  # 取昨天（默认）
    python -X utf8 scripts/wechat_kw31.py --date 2026-10-22
    python -X utf8 scripts/wechat_kw31.py --today          # 取今天（数据可能未齐）
    python -X utf8 scripts/wechat_kw31.py --backfill 7     # 补最近 7 天
    python -X utf8 scripts/wechat_kw31.py --keyword 购买    # 换关键词
    python -X utf8 scripts/wechat_kw31.py --no-rolling     # 不取 7 日滚动
    python -X utf8 scripts/wechat_kw31.py --show           # 只看本地台账

Chrome 生命周期：9222 已有调试 Chrome 就复用（**不动它**）；没有就自己起一个
无头 ChromeCDP-profile 的，干完活只杀自己起的进程树（不 taskkill /IM，
避免误杀用户日常 Chrome 窗口）。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atomicio                       # noqa: E402
import chrome_cdp_launch as L         # noqa: E402
import wechat_publish as wp           # noqa: E402

PORT = 9222
PROFILE = os.path.expandvars(r"%LOCALAPPDATA%\Google\ChromeCDP")
LEDGER = os.path.join(ROOT, "data", "wechat_kw31_daily.json")
DEFAULT_KEYWORD = "31"
# 消息关键词 Tab 的页面 URL（打开它只为拿到同源页面环境 + cgiData 对账用）
KW_PAGE = ("https://mp.weixin.qq.com/misc/messageanalysis"
           "?action=keyword&t=statistics/msg_keyword&type=-1&lang=zh_CN&token=%s")
DETACHED = 0x00000008  # DETACHED_PROCESS


# ---------------------------------------------------------------- Chrome 生命周期
def _probe_port():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % PORT, timeout=3) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def ensure_chrome():
    """返回 (proc or None)。None = 复用了已在跑的调试 Chrome（绝不杀）。"""
    L.clear_proxy()
    if _probe_port():
        print("[chrome] 9222 已在运行，复用（不重启、不动它）")
        return None
    L.remove_lock(PROFILE)
    chrome, label = L.pick_chrome()
    if not chrome:
        raise SystemExit("✗ 找不到 Chrome 可执行文件")
    args = L.build_args(chrome, PROFILE, port=PORT, headless=True)
    print("[chrome] 自起无头 Chrome（%s）" % label, flush=True)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            creationflags=DETACHED, close_fds=True)
    if not L.wait_cdp(proc, timeout=45, port=PORT):
        raise SystemExit("✗ 自起的 Chrome 没打开调试端口")
    return proc


def cleanup_chrome(proc):
    """只回收**自己起的**那棵进程树；复用的 Chrome 不碰。"""
    if proc is None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                   check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- 取数
FETCH_JS = r"""
(async function(){
  var tok=(location.href.match(/[?&]token=(\d+)/)||[])[1]||'';
  var url='/misc/messageanalysis?type=-1&action=keyword'
        +'&begin_date=%s&end_date=%s'
        +'&token='+tok+'&lang=zh_CN&f=json&ajax=1';
  var r=await fetch(url,{credentials:'include'});
  return await r.text();
})()
"""


def fetch_keyword_list(cdp, d1, d2):
    """页内 fetch 消息关键词列表。返回 [{keyword, exp_count}]（已按次数降序）。"""
    raw = cdp.eval(FETCH_JS % (d1, d2), refresh_context=True)
    if not raw:
        raise RuntimeError("接口无返回（页面可能未就绪）")
    try:
        data = json.loads(raw)
    except Exception:
        raise RuntimeError("接口返回非 JSON：%s" % str(raw)[:200])
    ret = (data.get("base_resp") or {}).get("ret")
    if ret != 0:
        raise RuntimeError("接口 ret=%s err=%s"
                           % (ret, (data.get("base_resp") or {}).get("err_msg")))
    lst = data.get("list") or []
    return [{"keyword": str(it.get("keyword", "")).strip(),
             "exp_count": it.get("exp_count")} for it in lst]


def find_keyword(lst, kw):
    """精确匹配（trim 后全等）。返回 {present, exp_count, rank, total_keywords}。"""
    for i, it in enumerate(lst):
        if it["keyword"] == kw:
            return {"present": True, "exp_count": it["exp_count"],
                    "rank": i + 1, "total_keywords": len(lst)}
    return {"present": False, "exp_count": None, "rank": None,
            "total_keywords": len(lst)}


def page_cgi_keyword_list(cdp):
    """读页面服务端内嵌的 30 日关键词列表（浏览器默认视图的那份），用于对账。"""
    raw = cdp.eval(
        "(function(){try{return JSON.stringify(window.cgiData.keyword_list||[]);}"
        "catch(e){return '[]';}})()", refresh_context=True)
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


# ---------------------------------------------------------------- 台账
def load_ledger():
    if not os.path.exists(LEDGER):
        return {}
    try:
        with open(LEDGER, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_record(date_str, entry):
    led = load_ledger()
    led[date_str] = entry
    atomicio.write_json(LEDGER, led, indent=2)


def fmt(stat):
    if stat is None:
        return "（未取）"
    if not stat.get("present"):
        return "未入榜（当日 0 或 1 次，平台滤除 <2）"
    return "%s 次（排行 #%s / 榜上 %s 词）" % (
        stat["exp_count"], stat["rank"], stat["total_keywords"])


def show():
    led = load_ledger()
    if not led:
        print("台账还是空的：%s" % LEDGER)
        return
    print("「%s」关键词统计台账（%s）" % (DEFAULT_KEYWORD, LEDGER))
    prev = None
    for d in sorted(led):
        e = led[d]
        day, r7 = e.get("day") or {}, e.get("rolling7") or {}
        delta = ""
        if prev is not None and day.get("present") and prev.get("present"):
            delta = "（较前一日 %+d）" % (day["exp_count"] - prev["exp_count"])
        print("  %s  当日 %-38s 7日滚动 %s%s"
              % (d, fmt(day), fmt(r7), delta))
        if day.get("present"):
            prev = day


# ---------------------------------------------------------------- 主流程
def run_dates(cdp, kw, dates, rolling=True):
    for d in dates:
        try:
            lst = fetch_keyword_list(cdp, d, d)
        except Exception as e:
            print("  %s 取数失败：%s" % (d, e))
            continue
        entry = {"keyword": kw, "day": find_keyword(lst, kw)}
        if rolling:
            d1 = (datetime.date.fromisoformat(d)
                  - datetime.timedelta(days=6)).isoformat()
            time.sleep(0.4)
            try:
                lst7 = fetch_keyword_list(cdp, d1, d)
                entry["rolling7"] = find_keyword(lst7, kw)
            except Exception as e:
                print("  %s 7 日滚动取数失败：%s" % (d, e))
        entry["fetched_at"] = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))).isoformat(
            timespec="seconds")
        save_record(d, entry)
        print("  %s 当日 %-40s 7日滚动 %s"
              % (d, fmt(entry["day"]), fmt(entry.get("rolling7"))))
        time.sleep(0.4)


def crosscheck(cdp, kw):
    """接口 vs 页面服务端内嵌数据（同 30 日窗口）对账，不一致只告警。"""
    end = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    begin = (datetime.date.fromisoformat(end)
             - datetime.timedelta(days=29)).isoformat()
    try:
        api = find_keyword(fetch_keyword_list(cdp, begin, end), kw)
        page = find_keyword(
            [{"keyword": str(it.get("keyword", "")).strip(),
              "exp_count": it.get("exp_count")}
             for it in page_cgi_keyword_list(cdp)], kw)
        if api.get("exp_count") == page.get("exp_count"):
            print("[对账] 接口与页面内嵌数据一致：30 日窗口 %s~%s「%s」= %s"
                  % (begin, end, kw, api.get("exp_count")))
        else:
            print("[对账][warn] 不一致！接口=%s 页面内嵌=%s（以浏览器肉眼所见为准，"
                  "请人工复核）" % (api.get("exp_count"), page.get("exp_count")))
    except Exception as e:
        print("[对账][warn] 跳过：%s" % e)


def main(argv=None):
    ap = argparse.ArgumentParser(description="每天取公众号消息关键词统计")
    ap.add_argument("--keyword", default=DEFAULT_KEYWORD,
                    help="要跟踪的关键词（默认 31）")
    ap.add_argument("--date", help="取指定日期 YYYY-MM-DD（默认昨天）")
    ap.add_argument("--today", action="store_true",
                    help="取今天（当天数据可能未齐，一般用来急救）")
    ap.add_argument("--backfill", type=int, metavar="N",
                    help="补最近 N 天（含目标日，逐日往前）")
    ap.add_argument("--no-rolling", action="store_true",
                    help="不取 7 日滚动口径")
    ap.add_argument("--show", action="store_true",
                    help="只打印本地台账，不起浏览器")
    args = ap.parse_args(argv)

    if args.show:
        show()
        return 0

    today = datetime.date.today()
    target = datetime.date.fromisoformat(args.date) if args.date else (
        today if args.today else today - datetime.timedelta(days=1))
    if args.backfill:
        dates = [(target - datetime.timedelta(days=i)).isoformat()
                 for i in range(args.backfill)]
    else:
        dates = [target.isoformat()]

    proc = ensure_chrome()
    cdp = wp.CDP(PORT)
    try:
        tid, token = wp._connect_mp(cdp)
        if not token:
            print("✗ 拿不到 token（三级兜底已跑完）：公众号登录态大概率真过期了，"
                  "需要人工在 ChromeCDP profile 里扫码恢复，本脚本不写台账。")
            return 2
        cdp.send("Page.enable")
        t = cdp.new_target(KW_PAGE % token)
        tid2 = t.get("id") or t.get("targetId")
        if not cdp.connect_target(tid2):
            print("✗ 连不上消息关键词页")
            return 2
        time.sleep(5)  # 等页面把 cgiData 渲染出来（对账要用）

        crosscheck(cdp, args.keyword)
        print("开始取「%s」：%s" % (args.keyword, ", ".join(dates)))
        run_dates(cdp, args.keyword, dates, rolling=not args.no_rolling)
        print("\n台账：%s（--show 可回看）" % LEDGER)
    finally:
        cleanup_chrome(proc)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
