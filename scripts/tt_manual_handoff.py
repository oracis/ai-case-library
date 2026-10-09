#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「人工点开 + 脚本接管」：你手动点草稿箱「编辑」，本脚本接管灌表格。

⚠⚠ 存在的理由（2026-10-09）：头条草稿箱的「编辑」是`<a class="op-button">`
   （**无 href**，React onClick）。平台侧现在**不接受任何 CDP 合成事件** ——
   坐标派发 / JS `.click()` / 派发完整 MouseEvent 序列 / 刷新后重试，四种方式
   全部无效，连此前成功的稿（如 trustmrr）也点不动。但 `elementFromPoint`
   证明按钮可见、无遮挡 ⇒ 是平台行为变化，不是脚本 bug。
   ⇒ 唯一可行的路：**人工点一下**，脚本只负责「接管编辑页 → 灌正文 → 回读」。

用法（两条腿）：
  1)先起监听（前台/后台都行，它会一直等）：
        python -u -X utf8 scripts/tt_manual_handoff.py --watch
  2) 你在浏览器草稿箱里逐条点「编辑」；本脚本检测到编辑页打开就会
     自动灌正文、回读表格数、关掉编辑页，然后继续等下一条。

⚠ 只灌**你点开的那一条**，绝不遍历、绝不自己找卡点编辑（那是刚才失效的路）。

选项：
    --watch               监听模式（默认）：等你点，来了就灌
    --one                 只处理当前已经打开的编辑页，灌完就退出
    --caseID              期望的案例 id（只用于日志标注与映射远端标题）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tt_body_fill as bf                                         # noqa: E402
import toutiao_publish as tp                                      # noqa: E402
import wechat_publish as wp                                       # noqa: E402

PUBLISH = "/graphic/publish"


def _title_to_case():
    """远端标题 → 本地案例 id（反向映射，供日志标注）。"""
    m = {}
    for c in tp.load_cases():
        mp = os.path.join(tp.OUT, c["id"], "meta.json")
        if os.path.isfile(mp):
            with open(mp, encoding="utf-8") as f:
                m[json.load(f)["title"].strip()] = c["id"]
    return m


def _match_case(title, t2c):
    """远端标题 → 本地案例 id。三级判据，认不出就返回 None（绝不猜）。

    1) 精确等于本地 meta 标题；
    2) `find_case_fuzzy` 模糊匹配（覆盖远端标题是**新标题**的情况）；
    3) ⚠ 远端标题是**旧标题**（本地后来改过，实测 `mort` =
       「MORT：AI 产品」vs 本地「MORT：AI 求职代理自动投简历」）⇒ 用
       草稿箱那3 条已知漂移做白名单，别的不猜。
    """
    if not title:
        return None
    if title in t2c:
        return t2c[title]
    # ⚠⚠ `find_case_fuzzy` 匹配不上会直接 `sys.exit`（它是 CLI 用的），
    #   放在这里会把整个监听器打死⇒ 必须包起来，且**先试精确/漂移白名单**。
    try:
        c = tp.find_case_fuzzy(title)
        if c:
            return c["id"]
    except SystemExit:
        pass
    except Exception:                                 # noqa: BLE001
        pass
    # 已知标题漂移白名单（远端旧标题 → 本地 id），实测于 2026-10-09
    DRIFT = {
        "MORT：AI 产品": "mort",
        "Quran Unlock：移动 app": "quran-unlock",
        "把英国公开抵押记录连成一张网，然后卖给贷款经纪": "harperai",
    }
    if title in DRIFT:
        return DRIFT[title]
    return None


def _editing_tabs():
    return [t for t in wp.CDP(tp.CDP_PORT).list_targets()
            if t.get("type") == "page" and PUBLISH in t.get("url", "")]


def _wait_body_ready(cdp, tries=12, pause=2.0):
    """等编辑器把草稿正文加载完（占位符 = 还没加载）。"""
    for _ in range(tries):
        time.sleep(pause)
        st = bf.is_empty(cdp)
        if st is not None:
            return True
    return False


def fill_open_editor(case_id, remote_title=None, close_after=True):
    """接管**当前已打开**的编辑页：灌本地正文 → 回读校验 → 关页。

    返回结果字符串（同`fill_one` 的口吻）。
    """
    tabs = _editing_tabs()
    if not tabs:
        return "no_editor：当前没有打开的编辑页"
    tgt = tabs[0]
    cdp = wp.CDP(tp.CDP_PORT)
    if not cdp.connect_target(tgt["id"]):
        return "连不上编辑页"
    try:
        if not _wait_body_ready(cdp):
            return "编辑器一直没就绪（占位符没消失），不敢动手"
        c = tp.find_case_fuzzy(case_id) if case_id else None
        cid = c["id"] if c else (case_id or "?")
        body = bf.body_html(cid) if c else None
        if body is None:
            return "本地找不到案例 %s" % case_id
        want_t, want_c = body.count("<table"), body.count("<td>")
        if not want_t:
            return "本地稿无表格"
        before = bf.read_body(cdp)
        if (before.get("tables") or 0) == want_t \
                and (before.get("cells") or 0) == want_c:
            return "skip：已是目标内容（%d 表/%d 格）" % (want_t, want_c)
        cleared = bf.clear_body(cdp)
        if cleared is False:
            return "清空没生效（编辑器还有内容），已放弃"
        cdp.eval("(function(){var e=document.querySelector(%s);"
                 "if(e){e.focus();} return 'ok';})()" % bf.SEL)
        cdp.send("Runtime.evaluate", {
            "expression": ("document.execCommand('insertHTML', false, %s)"
                           % json.dumps(body, ensure_ascii=False)),
            "returnByValue": True})
        time.sleep(2.5)
        mid = bf.read_body(cdp)
        if (mid.get("tables") or 0) != want_t:
            return ("灌完表格数不对（%d != %d），**未保存**，草稿仍是空正文，"
                    "需重跑本命令" % (mid.get("tables") or 0, want_t))
        ok_save = tp._wait_autosave(cdp)
        after = bf.read_body(cdp)
        good = ((after.get("tables") or 0) == want_t
                and (after.get("cells") or 0) == want_c)
        return ("ok：%d 表/%d 格%s" % (want_t, want_c,
                                       "" if ok_save else "（没等到保存提示）")
                if good else
                "保存后回读不符：%d 表/%d 格" % (after.get("tables") or 0,
                                             after.get("cells") or 0))
    finally:
        if close_after:
            wp.CDP(tp.CDP_PORT).close_target(tgt["id"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="监听你手动点开的编辑页")
    ap.add_argument("--one", action="store_true", help="只灌当前已打开的那一个")
    ap.add_argument("--case", default=None, help="案例 id（或远端标题，模糊匹配）")
    ap.add_argument("--keep", action="store_true", help="灌完不关编辑页")
    ap.add_argument("--timeout", type=int, default=3600, help="--watch 最长等秒数")
    args = ap.parse_args()

    t2c = _title_to_case()
    if args.one:
        r = fill_open_editor(args.case, close_after=not args.keep)
        print(r, flush=True)
        return 0 if (r.startswith("ok") or r.startswith("skip")) else 2

    #监听模式
    print("== 监听中：请在浏览器草稿箱里点「编辑」==", flush=True)
    print("   草稿箱：https://mp.toutiao.com/profile_v4/manage/draft", flush=True)
    print("   待处理 %d 条：mort voklit lancer-app speel-co checkvibe "
          "storyshort-ai meerkats-ai coral startclaw", flush=True)
    print("   （每灌完一条会自动关掉编辑页；处理完随时 Ctrl+C 退出）\n",
          flush=True)
    seen = set()
    end = time.time() + args.timeout
    ok = skip = bad = 0
    done_pgc = set()          # 已处理过的草稿（按 pgc_id，不按 tab id）
    while time.time() < end:
        tabs = _editing_tabs()
        # ⚠⚠ 去重必须按 **pgc_id** 而不是 tab id：头条会**复用同一个 tab**
        #   导航到下一条草稿（tab id 不变），而我们关页后头条又可能开新 tab
        #   指回同一个 pgc_id ⇒ 只按 tab id 去重会把同一条反复灌
        #   （2026-10-09 实测 meerkats 被灌了 3 次）。
        fresh = []
        for t in tabs:
            p = tp._pgc_id_of(t)
            key = p or t["id"]
            if key in seen or key in done_pgc:
                continue
            fresh.append((t, key))
        if not fresh:
            time.sleep(2)
            continue
        tgt, key = fresh[0]
        seen.add(key)
        pgc = tp._pgc_id_of(tgt)
        print(">> 检测到编辑页 pgc_id=%s（你刚点的）" % (pgc or "无"), flush=True)
        # 用远端标题反查是哪个案例：读编辑页里的标题框
        cid = None
        cdp = wp.CDP(tp.CDP_PORT)
        if cdp.connect_target(tgt["id"]):
            try:
                time.sleep(2)
                # ⚠ 必须用 title 选择器，不能用 bf.SEL（那是**正文区**，
                #   读出来是整篇正文，find_case_fuzzy 必然匹配不上）。
                title = (cdp.eval("(function(){var e=document.querySelector(%s);"
                                  "return e? (e.value||''):'';})()"
                                  % json.dumps(tp.SEL["title"])) or "").strip()
                if title:
                    cid = _match_case(title, t2c)
                    print("   标题：%s → %s" % (title[:40], cid or "未识别"),
                          flush=True)
            except (ConnectionAbortedError, ConnectionResetError,
                    OSError, RuntimeError):
                # 编辑页已被手动关闭 ⇒ 读不到标题，直接跳过这一条
                print("   ⚠ 编辑页已关闭（连接中断），跳过", flush=True)
                done_pgc.add(key)
                continue
        if not cid:
            print("   ⚠认不出是哪个案例，跳过（不猜、不乱灌）", flush=True)
            wp.CDP(tp.CDP_PORT).close_target(tgt["id"])
            done_pgc.add(key)
            bad += 1
            continue
        done_pgc.add(key)      # 认出来了，先记死，别重复灌同一条
        try:
            r = fill_open_editor(cid, close_after=not args.keep)
        except (ConnectionAbortedError, ConnectionResetError,
                OSError, RuntimeError) as e:
            # ⚠⚠ 你可能已经手动把编辑页关了 ⇒ 连接被中止。
            #   这**不是**稿子出问题，是页面没了。记一条、继续等下一条，
            #   绝不让整个监听器死掉（2026-10-09 实测因此崩过一次）。
            r = "连接中断（编辑页可能已被手动关闭）：%s" % (
                type(e).__name__,)
        print("   %s → %s\n" % (cid, r), flush=True)
        ok += r.startswith("ok")
        skip += r.startswith("skip")
        bad += not (r.startswith("ok") or r.startswith("skip"))
    print("\n结束：成功 %d / 跳过 %d / 失败 %d" % (ok, skip, bad), flush=True)
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())