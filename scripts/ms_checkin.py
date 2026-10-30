#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""魔搭（ModelScope）魔粒每日领取 + 余额记录。

为什么需要这个脚本
------------------
魔粒是 AIGC / API-Inference 的算力额度（轻量 0.5 / 主流 1 / 旗舰 2 粒每次）。

⚠ 关键：`ai_verify` 用的是 **Access Token（ms-xxx）直接打 API**，这种调用
**不产生"登录"行为**，所以每日额度不会因为跑脚本而自动到账。

🚨 2026-10-29 用户侧调研纠正了两个原有假设（都别再当事实用）：
   1. **没有官方签到 API**（只有余额查询的社区逆向接口）。任何"自动签到"脚本
      都是模拟网页操作，接口改版就失效 —— 所以本脚本走 CDP 真点页面，
      不做逆向接口调用。
   2. **额度不是"登录即到账"，要在魔粒页点「领取」**。原判据写的是
      「读到余额就算成功」，那等于**只登录不领取也报成功** —— 典型的假绿：
      日志里天天 OK，魔粒一分没多。
      现判据（见 checkin()）：必须**点到「领取」且余额比点之前增加**，
      或页面明确显示「今日已完成」（实测文案，每日发放两项都标这个），才算成功。
   额度本身也不是固定 200：官方动态调整，页面会显示「今日已领完」。

⚠ 为什么不能"解密 cookie 用 requests 打一次"（实测 2026-10-29）：
    日常 Chrome 的 modelscope cookie 是 **v20 前缀 = Chrome App-Bound
    加密**（Chrome 127+）。v20 的解密要过 Chrome 自己的 elevation
    service，DPAPI 拿 Local State 的 key 解不开。
    且 Chrome 136+ 拒绝在【默认 user-data-dir】上开调试端口。
    ⇒ 唯一可行路径：独立 CDP profile + 人工登录一次，之后无头复用。
    （与本项目微信公众号登录态同一范式，见 scripts/chrome_cdp_setup.md）

用法
----
    # 1) 首次：起有头 Chrome，人工登录一次（只需做一次）
    python scripts/ms_checkin.py --login

    # 2) 每天：无头开站点 → 点「领取」→ 记录余额
    python scripts/ms_checkin.py

    # 3) 只看状态不写日志 / 调试用
    python scripts/ms_checkin.py --probe
    python scripts/ms_checkin.py --headed        # 想亲眼看看页面

判据
----
状态只有四种，**别把 UNKNOWN 当失败，也别把 ALREADY 当新领取**：
    CLAIMED  点了「领取」且余额增加（记 before → after）
    ALREADY  没点到按钮，但页面写着「今日已完成 / 今日已领」= 今天已经领过
    NO_LOGIN profile 里没有魔搭会话（页面仍有「登录 / 注册」）→ 重跑 --login
    UNKNOWN  读不到判据：没找到领取入口 / 点了但余额没变（可能延迟到账）
退出码：0 = CLAIMED 或 ALREADY；2 = NO_LOGIN；3 = UNKNOWN。
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# Chrome 136+ 不给默认 profile 开调试端口，必须独立目录
PROFILE = os.environ.get("MS_CDP_PROFILE",
                         r"C:\Users\DELL\chrome-ms-profile")
PORT = int(os.environ.get("MS_CDP_PORT", "9333"))
USAGE_URL = "https://www.modelscope.cn/magicube/usage"
HOME_URL = "https://www.modelscope.cn/"
LOG_PATH = os.path.join(ROOT, "data", "ms_checkin_log.json")

# Git Bash 起的 python 缺这两个变量 → Chrome 会找不到自己的 DLL/目录
for _k, _v in (("LOCALAPPDATA", r"C:\Users\DELL\AppData\Local"),
               ("ProgramFiles", r"C:\Program Files")):
    os.environ.setdefault(_k, _v)


def log(msg):
    print(msg, flush=True)


def cdp_up(port=PORT):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % port, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def ensure_chrome(headless=True, port=PORT, url="about:blank"):
    """端口不通就自己起一个 Chrome。返回 (proc_or_None, launched_bool)。"""
    if cdp_up(port):
        return None, False
    import chrome_cdp_launch as L
    L.clear_proxy()
    chrome, label = L.pick_chrome()
    if chrome is None:
        raise SystemExit("找不到 Chrome 可执行文件")
    L.remove_lock(PROFILE)
    os.makedirs(PROFILE, exist_ok=True)
    args = L.build_args(chrome, PROFILE, extra=[url], port=port,
                        headless=headless)
    log("[ms_checkin] 起 Chrome(%s) port=%d headless=%s" % (label, port, headless))
    # ⚠ DETACHED_PROCESS：让 Chrome 脱离本进程的控制台/作业对象。
    # 沙箱会回收「调用 shell 退出时」的整棵进程树，不脱钩的话 --login
    # 开出来的窗口往往撑不到人去点登录就没了。PID 仍在我们手里，
    # stop_chrome 照样能按 PID 收掉。
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                            creationflags=flags)
    if not L.wait_cdp(proc, timeout=40, port=port):
        raise SystemExit("Chrome 启动失败（exit=%s）" % proc.poll())
    return proc, True


def stop_chrome(cdp, proc):
    """只杀自己拉起的那个 Chrome，绝不 taskkill /IM chrome.exe。"""
    try:
        if cdp is not None:
            cdp.close_browser()
            time.sleep(1)
    except Exception:
        pass
    if proc is None:
        return
    if proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/PID", str(proc.pid), "/T"],
                       check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    log("[ms_checkin] Chrome 已关闭 (pid=%s)" % proc.pid)


# ----------------------------------------------------------------------
# 页面判据
# ----------------------------------------------------------------------
JS_TEXT = "document.body ? document.body.innerText.slice(0, 4000) : ''"

JS_RESOURCES = (
    "performance.getEntriesByType('resource')"
    ".map(e => e.name)"
    ".filter(n => /magicube|magicube|point|credit|user|login|session/i.test(n))"
    ".slice(-40).join('\\n')"
)

JS_BALANCE = """
(function () {
  var t = document.body ? document.body.innerText : '';
  var out = [];
  // 找「魔粒」附近出现的数字
  var re = /([^\\n]{0,30}魔粒[^\\n]{0,40})/g, m;
  while ((m = re.exec(t)) !== null) { out.push(m[1].trim()); }
  return out.slice(0, 25).join(' || ');
})()
"""

JS_LOGGED_IN = """
(function () {
  var t = document.body ? document.body.innerText : '';
  // ⚠ 判据必须是「还有没有登录入口」，不能用「有没有『我的魔粒』字样」——
  // 未登录时页面标题就叫「我的魔粒」，按字样判会把未登录误报成已登录。
  // ⚠ 站点有中英两版文案（locale 可能落到英文），两边都要认，
  // 否则英文页会被判成 UNKNOWN。
  var loginBtn = /登录\\s*\\/\\s*注册|登录注册|login\\s*\\/\\s*register/i.test(t);
  var m = t.match(/(?:我的魔粒|My Magicubes?)\\s*\\n?\\s*([0-9][0-9,]*)/i);
  // 登录后顶栏会换成用户名/头像；未登录则是「登录 / 注册」
  var nick = document.querySelector('[class*="userInfo"],[class*="user-info"],'
    + '[class*="avatar"],[class*="userName"],[class*="nickname"]');
  return JSON.stringify({
    url: location.href,
    loginBtn: loginBtn,
    magicube: m ? m[1] : null,
    hasNickEl: !!nick,
    len: t.length,
    head: t.slice(0, 160)
  });
})()
"""


JS_CLAIM = """
(function () {
  function visible(el) {
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  var txt = document.body ? document.body.innerText : '';
  // 「今天已领过」的文案（2026-10-29 实测页面）：每日发放下两项会标
  // 「今日已完成」（注册并登录 200 / 绑定阿里云 50）。英文站与旧文案一并兜住，
  // 少认一种就会把「已领」判成 UNKNOWN —— 这条是实测补的，不是猜的。
  var claimed = /今日已完成|今日已领|已领取|明天再来|already claimed|claimed today|completed today/i.test(txt);
  var nodes = Array.prototype.slice.call(
    document.querySelectorAll('button, a, span, div'));
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    var s = (el.innerText || '').trim();
    // 只认短文案的独立按钮：父容器 innerText 会把整段页面都带进来
    if (!s || s.length > 8) continue;
    if (!/^(领取|立即领取|领取奖励|签到)$/.test(s)) continue;
    if (!visible(el)) continue;
    var cls = String(el.className || '');
    if (/disabled|is-disabled/.test(cls)) continue;
    el.click();
    return JSON.stringify({clicked: true, label: s, claimedText: claimed});
  }
  return JSON.stringify({clicked: false, claimedText: claimed});
})()
"""


def fetch_json_via_page(cdp, path):
    """在页面上下文里 fetch 站内接口，返回 (ok, text)。"""
    expr = (
        "(async () => { try { "
        "const r = await fetch('%s', {credentials:'include', "
        "headers:{'accept':'application/json'}}); "
        "const t = await r.text(); "
        "return JSON.stringify({status: r.status, body: t.slice(0,1500)}); "
        "} catch (e) { return JSON.stringify({error: String(e)}); } })()"
        % path
    )
    try:
        raw = cdp.eval(expr)
        return json.loads(raw)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


def probe(cdp):
    """调试：打印页面判据、资源列表、魔粒相关文案。"""
    import wechat_publish as wp
    t = cdp.new_target(USAGE_URL)
    tid = t.get("id") or t.get("targetId")
    if not cdp.connect_target(tid):
        log("[probe] connect_target 失败")
        return
    time.sleep(6)
    log("\n=== 登录判据 ===")
    try:
        log(cdp.eval(JS_LOGGED_IN))
    except Exception as e:
        log("  eval 失败: %s" % e)
    log("\n=== 魔粒相关文案 ===")
    try:
        log(str(cdp.eval(JS_BALANCE))[:1500])
    except Exception as e:
        log("  eval 失败: %s" % e)
    log("\n=== 正文前 800 字 ===")
    try:
        log(str(cdp.eval(JS_TEXT))[:800])
    except Exception as e:
        log("  eval 失败: %s" % e)
    log("\n=== 候选资源 URL ===")
    try:
        log(str(cdp.eval(JS_RESOURCES))[:2000])
    except Exception as e:
        log("  eval 失败: %s" % e)
    _ = wp  # 保持 import 语义：CDP 由调用方传入，此处仅确保模块可用


def _balance_of(info):
    """从登录判据里取余额数字；取不到返回 None（不猜）。"""
    try:
        return float(str(info.get("magicube")).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _read_page(cdp):
    """读一次页面判据 + 魔粒文案。返回 (info, lines)，读不到就抛。"""
    info = json.loads(cdp.eval(JS_LOGGED_IN) or "{}")
    try:
        lines = [x.strip() for x in str(cdp.eval(JS_BALANCE) or "").split("||")
                 if x.strip()]
    except Exception:
        lines = []
    return info, lines[:10]


def checkin(cdp):
    """打开首页 + 魔粒页 → 点「领取」→ 比对余额。返回结果 dict。

    🚨 判据为什么必须是「点了 + 余额变了」（2026-10-29 改）：
    官方没有签到 API，额度要在魔粒页**手动点「领取」**才到账。原来这里只要
    「页面读得到余额」就记 OK —— 那只证明登录成功，不证明领到了。后果是
    日志天天 OK、魔粒一分不涨，人还以为自动签到在工作（假绿的正典）。
    现在只有三种算成功：余额真的增加了 / 页面明确「今日已领」/ 点了按钮但
    余额没变 → 记 UNKNOWN（可能延迟到账），**绝不当成功**。

    ⚠ 返回统一成 dict（原来失败路径返回 (str, str)、成功路径返回 (str, dict)，
    调用方只有全部用 .get() 才不炸 —— 这是等着踩的坑）。
    """
    result = {"state": "UNKNOWN", "balance": None, "before": None,
              "delta": None, "lines": [], "detail": "", "clicked": False}

    # 1) 先开首页：带登录态访问站点（有些活动的判定挂在首页访问上）
    t = cdp.new_target(HOME_URL)
    tid = t.get("id") or t.get("targetId")
    if cdp.connect_target(tid):
        time.sleep(4)
    else:
        log("[warn] 首页 connect 失败，继续开魔粒页")

    # 2) 开魔粒页
    t = cdp.new_target(USAGE_URL)
    tid = t.get("id") or t.get("targetId")
    if not cdp.connect_target(tid):
        result["detail"] = "connect_target 失败"
        return result
    time.sleep(7)

    try:
        info, lines = _read_page(cdp)
    except Exception as e:
        result["detail"] = "登录判据 eval 失败: %s" % e
        return result

    result["lines"] = lines
    result["before"] = _balance_of(info)

    # ⚠ 顺序要紧：先判「有登录入口」= 未登录，再判其余。
    # 反过来（先判 URL 含 magicube / 有「我的魔粒」字样）会 100% 误报成功。
    if info.get("loginBtn"):
        result["state"] = "NO_LOGIN"
        result["detail"] = "页面仍有「登录 / 注册」入口，profile 里没有魔搭会话"
        return result
    if result["before"] is None:
        result["detail"] = ("没拿到余额判据 magicube=%s（页面可能没渲染完）"
                            % info.get("magicube"))
        return result

    # 3) 点「领取」（找不到按钮不报错：可能今天已经领过）
    try:
        claim = json.loads(cdp.eval(JS_CLAIM) or "{}")
    except Exception as e:
        claim = {}
        log("[warn] 领取按钮判据 eval 失败: %s" % e)
    result["clicked"] = bool(claim.get("clicked"))
    if result["clicked"]:
        log("[..] 已点「%s」，等 5 秒看余额变化" % claim.get("label"))
        time.sleep(5)
        try:
            after_info, after_lines = _read_page(cdp)
            result["balance"] = _balance_of(after_info)
            if after_lines:
                result["lines"] = after_lines
        except Exception as e:
            log("[warn] 点完重读余额失败: %s" % e)
    else:
        result["balance"] = result["before"]

    # 4) 定性：余额增加 > 点了按钮 > 「今日已领」> 什么都不是
    after = result["balance"]
    if after is not None and result["before"] is not None and after > result["before"]:
        result["state"] = "CLAIMED"
        result["delta"] = after - result["before"]
        result["detail"] = ("领取成功：%g → %g（+%g）"
                            % (result["before"], after, result["delta"]))
    elif result["clicked"]:
        result["state"] = "UNKNOWN"
        result["detail"] = ("点了「%s」但余额没变（%s → %s），可能延迟到账，"
                            "去页面确认一下" % (claim.get("label"),
                                            result["before"], after))
    elif claim.get("claimedText"):
        result["state"] = "ALREADY"
        result["detail"] = ("页面显示今日已完成（余额 %g）" % result["before"])
    else:
        result["detail"] = ("没找到「领取」按钮，也没有「今日已领」文案"
                            "（余额 %g）—— 可能改版，先 --probe 看页面"
                            % result["before"])
    return result


def append_log(entry):
    """当日只记一条，重复跑覆盖当天记录。"""
    data = {}
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    today = time.strftime("%Y-%m-%d")
    data.setdefault("days", {})
    data["days"][today] = entry
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    tmp = LOG_PATH + ".tmp"
    # 显式 newline=""：Windows 上默认会把 \n 翻成 \r\n，同一份 JSON 换台机器
    # 生成就是另一套行尾，diff 全是噪音（项目写盘范式见 multiplatform/state.py）。
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOG_PATH)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true",
                    help="起有头 Chrome 供人工登录（只需一次），不关浏览器")
    ap.add_argument("--probe", action="store_true",
                    help="只打印页面判据，不写日志")
    ap.add_argument("--headed", action="store_true",
                    help="本次用有头模式（默认无头）")
    ap.add_argument("--keep", action="store_true",
                    help="跑完不关 Chrome")
    args = ap.parse_args()

    if args.login:
        ensure_chrome(headless=False, url=USAGE_URL)
        log("")
        log("=" * 60)
        log(" 请在刚打开的 Chrome 里登录 modelscope.cn")
        log(" 登录后浏览器保持开着即可，Cookie 已存进：%s" % PROFILE)
        log(" 然后跑：python scripts/ms_checkin.py --probe")
        log("=" * 60)
        return 0

    proc, launched = ensure_chrome(headless=not args.headed)
    import wechat_publish as wp
    cdp = wp.CDP(port=PORT)
    try:
        if args.probe:
            probe(cdp)
            return 0
        res = checkin(cdp)
        state = res["state"]
        entry = {
            "state": state,
            "balance": res["balance"],
            "before": res["before"],
            "delta": res["delta"],
            "detail": res["detail"],
            "at": time.strftime("%H:%M:%S"),
        }
        for ln in res["lines"][:6]:
            log("    · %s" % ln)

        if state == "CLAIMED":
            log("[✓] 领取成功  %s" % res["detail"])
            append_log(entry)
            log("    已写入 %s" % LOG_PATH)
            return 0
        if state == "ALREADY":
            log("[=] 今天已经领过了：%s" % res["detail"])
            log("    （这不是失败，但也别当成刚领到）")
            append_log(entry)
            return 0
        if state == "NO_LOGIN":
            log("[✗] NO_LOGIN：该 profile 里没有魔搭登录态。")
            log("    跑一次：python scripts/ms_checkin.py --login")
            append_log(entry)
            return 2
        log("[?] UNKNOWN：没拿到判据（没找到领取入口 / 余额没变 / 页面没渲染完）。")
        log("    判据原话：%s" % res["detail"])
        log("    用 --probe 看页面到底返回了什么，别直接当失败。")
        append_log(entry)
        return 3
    finally:
        # ⚠ 只关「本次自己拉起」的 Chrome。若端口上本来就有 Chrome（比如
        # --login 留下的那个有头窗口/用户日常在用），close_browser 会把人家
        # 的窗口一起干掉 —— 这是不可接受的副作用。
        if launched and not args.keep:
            stop_chrome(cdp, proc)
        elif launched:
            log("[ms_checkin] --keep：保留 Chrome (pid=%s)" % proc.pid)
        else:
            log("[ms_checkin] 复用已有 Chrome，不关闭")


if __name__ == "__main__":
    sys.exit(main())
