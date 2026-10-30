#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两朵云 CDP 探针：腾讯云 / 阿里云开发者社区 登录 + 编辑器字段发现。

为什么有这个脚本：CSDN 同步助手 MCP 只存草稿、丢弃 cover/摘要/社区，
两朵云发表前的收尾项用 CDP 操作：
  腾讯云 = 发布弹窗「摘要（一键提取）」+「文章封面（正文图选）」
  阿里云 = 「AI 生成摘要」+「社区归属」+「文章图片」

用法:
  python scripts/yun_cdp.py login [--port 9228] [--wait 600]
      起独立 profile（有头）打开两朵云，等用户扫码登录；登录成功后自动跑
      编辑器发现（候选 URL 探测 + 列表页链接抓取 + 截图）。--no-probe 跳过。
  python scripts/yun_cdp.py cookies [--port 9228]
      对已起实例 dump 两域 cookie 名（登录态诊断）。

红线：探针只读 DOM、不点任何「发布」按钮；viewport 钉死 1440x900。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import chrome_cdp_launch as L                     # noqa: E402
from juejin_publish import _CDP                   # noqa: E402  stdlib CDP 客户端

PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-yun-profile")
PORT = 9228
SHOT_DIR = os.path.join(ROOT, "_shots")
TMP_DIR = os.path.join(ROOT, "tmp")

TENCENT_HOME = "https://cloud.tencent.com/developer"
ALIYUN_HOME = "https://developer.aliyun.com"

# 编辑器候选 URL（draft 占位）；哪个真加载出编辑器，实测后固化
TENCENT_CANDIDATES = [
    "https://cloud.tencent.com/developer/article/edit/%s",
    "https://cloud.tencent.com/developer/article/write/%s",
    "https://console.cloud.tencent.com/developer/article/edit/%s",
]
ALIYUN_CANDIDATES = [
    "https://developer.aliyun.com/article/edit/%s",
    "https://developer.aliyun.com/article/new#/?draftId=%s",
]

# 摘要/封面/社区/图片 相关按钮关键词（只在输出里高亮，绝不点击）
KEYWORDS = ("提取", "摘要", "封面", "正文图", "社区", "标签", "图片",
            "发布", "保存", "AI")

EDITOR_JS = r"""
JSON.stringify({
  url: location.href,
  title: document.title,
  editables: document.querySelectorAll('[contenteditable=true]').length,
  textareas: document.querySelectorAll('textarea').length,
  fileInputs: document.querySelectorAll('input[type=file]').length,
  titleInputs: [...document.querySelectorAll('input')]
    .filter(i => /标题|title/i.test((i.placeholder||'') + ' ' + (i.name||'')))
    .map(i => (i.placeholder || i.name)).slice(0, 3),
  buttons: [...document.querySelectorAll('button, [role=button], .btn, a.btn')]
    .map(b => (b.textContent || '').trim().replace(/\s+/g, ' '))
    .filter(t => t && t.length <= 14).slice(0, 80)
})
"""

LIST_LINKS_JS = r"""
JSON.stringify([...document.querySelectorAll('a[href]')]
  .map(a => a.getAttribute('href'))
  .filter(h => h && h.includes('article'))
  .slice(0, 40))
"""


def cookie_names(cdp, domain_part):
    """某域下全部 cookie 名（直接调 Network.getAllCookies；juejin 的 _CDP.cookies()
    返回的是拼好的字符串拿不到 domain，这里自己发）。"""
    try:
        cdp.send("Network.enable")
        r = cdp.send("Network.getAllCookies")
    except Exception:                                          # noqa: BLE001
        return []
    items = (r.get("result") or {}).get("cookies") or []
    return sorted({c.get("name", "") for c in items
                   if domain_part in (c.get("domain") or "") and c.get("name")})


def tencent_logged(names):
    return bool({"uin", "login_type", "extern_uid", "account_login_type",
                 "wx_uin"} & set(names))


def aliyun_logged(names):
    return any(n.startswith("login_aliyunid") for n in names)


def shot(cdp, path):
    try:
        r = cdp.send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["result"]["data"]))
        print("  📷 %s" % os.path.relpath(path, ROOT))
    except Exception as e:                                     # noqa: BLE001
        print("  (截图失败: %s)" % str(e)[:80])


def pin_viewport(cdp, w=1440, h=900):
    """按 viewport 比例点击的坐标必须先把视口钉死（小红书 headless 教训）。"""
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})


def open_page(cdp, url, settle=3.5):
    tid = cdp.open(url)
    time.sleep(settle)
    return tid


def dump_editor(cdp, tag, url):
    """打开候选 URL，dump 编辑器形状；返回 (final_url, 摘要dict, 疑似字段)。"""
    tid = open_page(cdp, url)
    pin_viewport(cdp)
    time.sleep(1.0)
    raw = cdp.eval(EDITOR_JS) or "{}"
    try:
        info = json.loads(raw)
    except Exception:                                          # noqa: BLE001
        info = {"url": None, "raw_head": str(raw)[:120]}
    interesting = [b for b in info.get("buttons", [])
                   if any(k in b for k in KEYWORDS)]
    shot(cdp, os.path.join(SHOT_DIR, "yun_%s.png" % tag))
    with open(os.path.join(TMP_DIR, "yun_probe_%s.json" % tag),
              "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    editorish = info.get("editables", 0) > 0 or info.get("textareas", 0) > 0
    print("  [%s] %s" % (tag, info.get("url") or "?"))
    print("      title=%s | editable区=%s textarea=%s file=%s | 编辑器=%s"
          % ((info.get("title") or "")[:40], info.get("editables"),
             info.get("textareas"), info.get("fileInputs"), editorish))
    if interesting:
        print("      ★ 关键按钮: %s" % " | ".join(interesting[:12]))
    try:
        cdp.send_browser("Target.closeTarget", {"targetId": tid})
    except Exception:                                          # noqa: BLE001
        pass
    time.sleep(0.5)
    return info


def dump_links(cdp, tag, url, settle=4.0):
    """打开列表页，抓含 article 的链接（找草稿编辑入口）。"""
    tid = open_page(cdp, url, settle)
    links = cdp.eval(LIST_LINKS_JS) or "[]"
    try:
        links = json.loads(links)
    except Exception:                                          # noqa: BLE001
        links = []
    print("  [%s] %s → %d 条 article 链接" % (tag, url, len(links)))
    for h in links[:15]:
        print("      %s" % h)
    with open(os.path.join(TMP_DIR, "yun_links_%s.json" % tag),
              "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=1)
    try:
        cdp.send_browser("Target.closeTarget", {"targetId": tid})
    except Exception:                                          # noqa: BLE001
        pass
    return links


def run_probe(cdp, tencent_draft, aliyun_draft):
    print("\n=== 编辑器发现 ===")
    if tencent_draft:
        for tpl in TENCENT_CANDIDATES:
            dump_editor(cdp, "tc_" + str(tencent_draft), tpl % tencent_draft)
        dump_links(cdp, "tc_list", TENCENT_HOME)
    if aliyun_draft:
        for tpl in ALIYUN_CANDIDATES:
            dump_editor(cdp, "al_" + str(aliyun_draft), tpl % aliyun_draft)
        dump_links(cdp, "al_list", ALIYUN_HOME)


def wait_login(cdp, wait, probe, tencent_draft, aliyun_draft):
    print("等待两朵云登录（每 5 秒检测，最多 %d 秒）…" % wait)
    deadline = time.time() + wait
    tc_ok = al_ok = False
    while time.time() < deadline:
        time.sleep(5)
        tc_names = cookie_names(cdp, "cloud.tencent.com")
        al_names = cookie_names(cdp, "aliyun.com")
        if not tc_ok:
            tc_ok = tencent_logged(tc_names)
            if tc_ok:
                print("✓ 腾讯云已登录（cookie: %s）" % ", ".join(tc_names[:8]))
        if not al_ok:
            al_ok = aliyun_logged(al_names)
            if al_ok:
                print("✓ 阿里云已登录（cookie: %s）" % ", ".join(al_names[:8]))
        if tc_ok and al_ok:
            print("✓ 两朵云全部就绪")
            if probe:
                run_probe(cdp, tencent_draft, aliyun_draft)
            return 0
    print("⏰ 超时。当前 cookie 名：")
    print("  cloud.tencent.com: %s" % (", ".join(tc_names[:12]) or "(空)"))
    print("  aliyun.com: %s" % (", ".join(al_names[:12]) or "(空)"))
    return 2


def cmd_login(args):
    L.clear_proxy()
    L.remove_lock(PROFILE)
    chrome, label = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    proc = subprocess.Popen(
        L.build_args(chrome, PROFILE, port=args.port, headless=False),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=args.port):
        raise SystemExit("Chrome CDP 没起来")
    cdp = _CDP(args.port)
    try:
        open_page(cdp, TENCENT_HOME, 1.0)
        open_page(cdp, ALIYUN_HOME, 1.0)
        print("已打开两朵云 —— 请在这个 Chrome 窗口里分别登录腾讯云和阿里云"
              "（右上角登录，扫码/手机号均可）。")
        rc = wait_login(cdp, args.wait, not args.no_probe,
                        args.tencent_draft, args.aliyun_draft)
        if rc == 0 and not args.keep:
            print("（登录态已存进独立 profile，后续探针直接复用，现在关浏览器）")
            time.sleep(1.0)
        return rc
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        if not args.keep:
            try:
                proc.terminate()
            except Exception:                                  # noqa: BLE001
                pass


def cmd_cookies(args):
    cdp = _CDP(args.port)
    try:
        print("cloud.tencent.com: %s" % ", ".join(cookie_names(cdp, "cloud.tencent.com")))
        print("developer.aliyun.com 系: %s" % ", ".join(cookie_names(cdp, "aliyun.com")))
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# 第二轮：阿里云编辑器深挖 DOM / 腾讯云创作中心抓草稿入口
# --------------------------------------------------------------------------

AL_EDITOR_JS = r"""
JSON.stringify({
  url: location.href,
  textareas: [...document.querySelectorAll('textarea')].map(t => ({
    ph: t.placeholder, name: t.name, id: t.id, cls: (t.className||'').slice(0,50)})),
  inputs: [...document.querySelectorAll('input')].map(i => ({
    type: i.type, ph: i.placeholder, name: i.name, id: i.id,
    value: i.type === 'file' ? '' : (i.value||'').slice(0,40)})).slice(0, 40),
  buttons: [...document.querySelectorAll('button, [role=button]')]
    .map(b => (b.textContent||'').trim().replace(/\s+/g,' '))
    .filter(t => t && t.length <= 20).slice(0, 100),
  leafHits: [...document.querySelectorAll('div,span,label,p')]
    .filter(e => e.children.length === 0 &&
      /摘要|社区|封面|专栏|图片|AI/.test(e.textContent||''))
    .map(e => ({tag: e.tagName, txt: (e.textContent||'').trim().slice(0, 30),
                cls: (e.className||'').toString().slice(0, 40)})).slice(0, 40)
})
"""

ALL_LINKS_JS = r"""
JSON.stringify([...document.querySelectorAll('a[href]')]
  .map(a => a.getAttribute('href'))
  .filter(h => h && /draft|edit|write|creative|article-manage|manage/i.test(h))
  .slice(0, 60))
"""


def _launch_profile(port):
    """起带登录态的 profile 并连上 CDP（探针复用）。"""
    L.clear_proxy()
    L.remove_lock(PROFILE)
    chrome, _ = L.pick_chrome()
    if not chrome:
        raise SystemExit("找不到 Chrome")
    proc = subprocess.Popen(
        L.build_args(chrome, PROFILE, port=port, headless=False),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=45, port=port):
        raise SystemExit("Chrome CDP 没起来")
    return proc, _CDP(port)


def _close(cdp, tid):
    try:
        cdp.send_browser("Target.closeTarget", {"targetId": tid})
    except Exception:                                          # noqa: BLE001
        pass


def cmd_al_editor(args):
    """阿里云编辑器深挖：摘要/社区/图片控件全量 dump + 截图。"""
    proc, cdp = _launch_profile(args.port)
    try:
        url = "https://developer.aliyun.com/article/new#/?draftId=%s" % args.draft
        tid = open_page(cdp, url, settle=6.0)
        pin_viewport(cdp)
        time.sleep(1.5)
        raw = cdp.eval(AL_EDITOR_JS) or "{}"
        info = json.loads(raw)
        with open(os.path.join(TMP_DIR, "yun_al_editor.json"), "w",
                  encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        shot(cdp, os.path.join(SHOT_DIR, "yun_al_editor.png"))
        print("URL: %s" % info.get("url"))
        print("\n-- textarea ×%d --" % len(info.get("textareas", [])))
        for t in info.get("textareas", []):
            print("   ph=%r name=%r id=%r cls=%r" % (t.get("ph"), t.get("name"),
                                                     t.get("id"), t.get("cls")))
        print("\n-- input ×%d --" % len(info.get("inputs", [])))
        for i in info.get("inputs", []):
            print("   type=%s ph=%r name=%r id=%r val=%r"
                  % (i.get("type"), i.get("ph"), i.get("name"),
                     i.get("id"), i.get("value")))
        print("\n-- button ×%d --" % len(info.get("buttons", [])))
        print("   %s" % " | ".join(info.get("buttons", [])))
        print("\n-- 含关键词叶子节点 ×%d --" % len(info.get("leafHits", [])))
        for e in info.get("leafHits", []):
            print("   <%s> %r cls=%r" % (e.get("tag"), e.get("txt"), e.get("cls")))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_nav(args):
    """腾讯云创作中心导航抓取：找草稿编辑页真实 URL。"""
    proc, cdp = _launch_profile(args.port)
    try:
        for tag, url in [("console", "https://console.cloud.tencent.com/developer"),
                         ("creative", "https://cloud.tencent.com/developer/creative-center"),
                         ("me", "https://cloud.tencent.com/developer/user/me")]:
            tid = open_page(cdp, url, settle=6.0)
            pin_viewport(cdp)
            time.sleep(1.0)
            raw = cdp.eval(ALL_LINKS_JS) or "[]"
            try:
                links = json.loads(raw)
            except Exception:                                  # noqa: BLE001
                links = []
            final = cdp.eval("location.href") or "?"
            print("[%s] %s → %s | %d 条命中" % (tag, url, final, len(links)))
            for h in links[:20]:
                print("    %s" % h)
            shot(cdp, os.path.join(SHOT_DIR, "yun_tc_nav_%s.png" % tag))
            _close(cdp, tid)
            time.sleep(0.5)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_al_drafts(args):
    """阿里云：打开 /article/new 点「草稿箱」，抓草稿列表链接。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://developer.aliyun.com/article/new", settle=6.0)
        pin_viewport(cdp)
        raw = cdp.eval(r"""
(function(){
  const t = document.querySelector('.draft-box');
  if (!t) return '"no .draft-box"';
  t.click();
  return '"clicked .draft-box"';
})()""")
        print("草稿箱按钮: %s" % raw)
        time.sleep(4.0)
        info = json.loads(cdp.eval(r"""
JSON.stringify({
  url: location.href,
  links: [...document.querySelectorAll('a[href]')]
    .map(a => a.getAttribute('href'))
    .filter(h => h && /article|draft/i.test(h)).slice(0, 40),
  drawer: [...document.querySelectorAll('div,li,p,a,button,span')]
    .filter(e => /编辑|删除|草稿|发布|继续/.test(e.textContent||'')
      && e.textContent.trim().length <= 14 && e.offsetParent !== null)
    .map(e => ({tag: e.tagName, txt: (e.textContent||'').trim().slice(0,14),
                cls: (e.className||'').toString().slice(0,40)})).slice(0, 40)
})""") or "{}")
        shot(cdp, os.path.join(SHOT_DIR, "yun_al_drafts.png"))
        print("final: %s" % info.get("url"))
        print("\n-- 链接 ×%d --" % len(info.get("links", [])))
        for h in info.get("links", []):
            print("    %s" % h)
        # 草稿箱对话框内容
        dlg = json.loads(cdp.eval(r"""
(function(){
  const d = document.querySelector('.next-dialog');
  if (!d) return '"no-dialog"';
  return JSON.stringify({
    text: d.textContent.replace(/\s+/g, ' ').slice(0, 600),
    links: [...d.querySelectorAll('a[href]')].map(a =>
      a.getAttribute('href')).slice(0, 20),
    btns: [...d.querySelectorAll('button, a, [role=button]')].map(b =>
      (b.textContent||'').trim()).filter(t => t && t.length <= 16).slice(0, 30)
  });
})()""") or "{}")
        if isinstance(dlg, str):
            print("\n草稿箱对话框: %s" % dlg)
        else:
            print("\n-- 草稿箱对话框 --")
            print("    text: %s" % dlg.get("text", "")[:400])
            print("    links: %s" % dlg.get("links"))
            print("    btns: %s" % " | ".join(dlg.get("btns", [])))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_deep(args):
    """腾讯云 console/developer 深挖：iframe + 全部链接 + 关键词按钮。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://console.cloud.tencent.com/developer",
                        settle=8.0)
        pin_viewport(cdp)
        info = json.loads(cdp.eval(r"""
JSON.stringify({
  url: location.href,
  iframes: [...document.querySelectorAll('iframe')].map(f => f.src).slice(0, 10),
  links: [...document.querySelectorAll('a[href]')]
    .map(a => a.getAttribute('href')).filter(Boolean).slice(0, 80),
  hits: [...document.querySelectorAll('a,button,span,div')]
    .filter(e => /创作中心|内容管理|草稿|我的文章|发表|投稿/.test(e.textContent||'')
      && e.children.length === 0)
    .map(e => ({tag: e.tagName, txt: (e.textContent||'').trim().slice(0, 20)}))
    .slice(0, 30)
})""") or "{}")
        print("final: %s" % info.get("url"))
        print("\n-- iframe ×%d --" % len(info.get("iframes", [])))
        for s in info.get("iframes", []):
            print("    %s" % s)
        print("\n-- 关键词命中 ×%d --" % len(info.get("hits", [])))
        for e in info.get("hits", []):
            print("    <%s> %r" % (e.get("tag"), e.get("txt")))
        print("\n-- 全部链接 ×%d --" % len(info.get("links", [])))
        for h in info.get("links", [])[:40]:
            print("    %s" % h)
        shot(cdp, os.path.join(SHOT_DIR, "yun_tc_deep.png"))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_al_debug(args):
    """调试：dump 页面上所有含「草稿」的元素与 iframe 数。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://developer.aliyun.com/article/new", settle=6.0)
        info = json.loads(cdp.eval(r"""
JSON.stringify({
  iframes: [...document.querySelectorAll('iframe')].map(f => f.src.slice(0,80)),
  hits: [...document.querySelectorAll('*')]
    .filter(e => /草稿/.test(e.textContent||'')
      && !(e.firstElementChild && /草稿/.test(e.firstElementChild.textContent||'')))
    .map(e => ({tag: e.tagName, txt: (e.textContent||'').trim().slice(0,30),
                cls: (e.className||'').toString().slice(0,50),
                id: e.id}))
    .slice(0, 30)
})""") or "{}")
        print("iframes: %s" % info.get("iframes"))
        for e in info.get("hits", []):
            print("    <%s> %r cls=%r id=%r" % (e.get("tag"), e.get("txt"),
                                                e.get("cls"), e.get("id")))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_creator(args):
    """腾讯云社区找创作中心/内容管理入口（--url 可换成创作中心内页）。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, args.url, settle=7.0)
        pin_viewport(cdp)
        info = json.loads(cdp.eval(r"""
JSON.stringify({
  url: location.href,
  hits: [...document.querySelectorAll('a,button,span,div,li')]
    .filter(e => /创作中心|内容管理|草稿|我的文章|发表文章|投稿/.test(e.textContent||'')
      && (e.textContent||'').trim().length <= 12)
    .map(e => ({tag: e.tagName, txt: (e.textContent||'').trim(),
                href: e.getAttribute && (e.getAttribute('href')||'')}))
    .slice(0, 30),
  links: [...document.querySelectorAll('a[href]')]
    .map(a => a.getAttribute('href'))
    .filter(h => h && /creator|manage|draft|write|my|user|center/i.test(h))
    .slice(0, 40)
})""") or "{}")
        print("final: %s" % info.get("url"))
        print("\n-- 关键词命中 ×%d --" % len(info.get("hits", [])))
        for e in info.get("hits", []):
            print("    <%s> %r href=%s" % (e.get("tag"), e.get("txt"), e.get("href")))
        print("\n-- 链接 ×%d --" % len(info.get("links", [])))
        for h in info.get("links", []):
            print("    %s" % h)
        shot(cdp, os.path.join(SHOT_DIR, "yun_tc_creator.png"))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_drafts(args):
    """腾讯云：创作中心 → 文章管理 → 草稿箱标签，dump 草稿卡片。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://cloud.tencent.com/developer/creator/article",
                        settle=7.0)
        pin_viewport(cdp)
        raw = cdp.eval(r"""
(function(){
  const tab = [...document.querySelectorAll('a,li,span,div')]
    .find(e => /草稿箱\(\d+\)/.test((e.textContent||'').trim())
      && e.textContent.trim().length <= 10);
  if (!tab) return '"no-draft-tab"';
  tab.click();
  return '"clicked"';
})()""")
        print("草稿箱标签: %s" % raw)
        time.sleep(3.5)
        info = json.loads(cdp.eval(r"""
JSON.stringify({
  cards: [...document.querySelectorAll('li,div[class*=item],div[class*=card]')]
    .filter(e => e.offsetParent !== null
      && /编辑|删除|草稿/.test(e.textContent||'')
      && e.textContent.trim().length < 200
      && !(e.querySelector('li,div[class*=item],div[class*=card]')))
    .map(e => e.textContent.replace(/\s+/g, ' ').trim().slice(0, 120)).slice(0, 15),
  btns: [...document.querySelectorAll('button, a')]
    .filter(e => e.offsetParent !== null
      && /^(编辑|删除|发表|查看|管理)$/.test((e.textContent||'').trim()))
    .map(e => ({txt: e.textContent.trim(),
                cls: (e.className||'').toString().slice(0, 40)})).slice(0, 20)
})""") or "{}")
        shot(cdp, os.path.join(SHOT_DIR, "yun_tc_drafts.png"))
        print("\n-- 草稿卡片 ×%d --" % len(info.get("cards", [])))
        for c in info.get("cards", []):
            print("    %s" % c)
        print("\n-- 操作按钮 ×%d --" % len(info.get("btns", [])))
        for b in info.get("btns", []):
            print("    %r cls=%r" % (b.get("txt"), b.get("cls")))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_edit(args):
    """腾讯云：草稿箱点第 N 个「编辑」，看编辑器是否载入草稿。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://cloud.tencent.com/developer/creator/article",
                        settle=7.0)
        pin_viewport(cdp)
        cdp.eval(r"""
[...document.querySelectorAll('a,li,span,div')]
  .find(e => /草稿箱\(\d+\)/.test((e.textContent||'').trim())
    && e.textContent.trim().length <= 10).click()""")
        time.sleep(3.5)
        raw = cdp.eval(r"""
(function(){
  // 编辑按钮 = SPAN.cdc-svg-icon-text（外层 SPAN.cdc-svg-icon-con），非 button/a
  const btns = [...document.querySelectorAll('.cdc-svg-icon-con')]
    .filter(e => e.offsetParent !== null
      && (e.textContent||'').trim() === '编辑');
  if (btns.length <= %d) return JSON.stringify({err: 'only ' + btns.length + ' edit btns'});
  const r = btns[%d].getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width / 2, y: r.y + r.height / 2,
                         n: btns.length});
})()""" % (args.index, args.index))
        print("编辑按钮[%d]: %s" % (args.index, raw))
        pos = json.loads(raw)
        if "err" in pos:
            return 2
        # 合成 click 触发不了框架 handler（公众号 ProseMirror 同款坑）→ 真实鼠标事件
        for tp in ("mousePressed", "mouseReleased"):
            cdp.send("Input.dispatchMouseEvent",
                     {"type": tp, "x": pos["x"], "y": pos["y"],
                      "button": "left", "clickCount": 1})
        time.sleep(6.0)
        # 编辑可能开新标签页 —— 枚举 target 找新页面并挂上去
        before_ids = {t.get("id") for t in cdp._http("/json")}
        del before_ids  # 在 open 之前记录才有意义，这里直接找 editor 形状的新页
        pages = [t for t in cdp._http("/json") if t.get("type") == "page"]
        print("当前页面:")
        for t in pages:
            print("    %s | %s" % (t.get("url", "")[:90],
                                   (t.get("title") or "")[:30]))
        editor_pages = [t for t in pages
                        if t.get("url", "") != (
                            "https://cloud.tencent.com/developer/creator/"
                            "article?type=draft")
                        and not t.get("url", "").startswith("chrome://")]
        if len(pages) > 2 and editor_pages:
            new = editor_pages[-1]
            print("→ 挂上新页面: %s" % new.get("url"))
            from juejin_publish import _WS
            cdp.ws = _WS(new["webSocketDebuggerUrl"])
        print("final URL: %s" % (cdp.eval("location.href") or "?"))
        info = json.loads(cdp.eval(EDITOR_JS) or "{}")
        shot(cdp, os.path.join(SHOT_DIR, "yun_tc_edit.png"))
        print("title=%r editables=%s textareas=%s file=%s"
              % ((info.get("title") or "")[:40], info.get("editables"),
                 info.get("textareas"), info.get("fileInputs")))
        btns = info.get("buttons", [])
        print("buttons: %s" % " | ".join(btns[:40]))
        # 编辑器正文有没有内容
        body_head = cdp.eval(r"""
(function(){
  const ed = document.querySelector('[contenteditable=true]');
  return ed ? ed.textContent.trim().slice(0, 120) : '(无 contenteditable)';
})()""")
        print("正文头部: %r" % body_head)
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_al_edit(args):
    """阿里云：/article/new → 草稿箱 → 点标题载入草稿，验证字段。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://developer.aliyun.com/article/new", settle=6.0)
        pin_viewport(cdp)
        cdp.eval("document.querySelector('.draft-box').click()")
        time.sleep(3.0)
        raw = cdp.eval(r"""
(function(){
  const d = document.querySelector('.next-dialog');
  if (!d) return '"no-dialog"';
  const items = [...d.querySelectorAll('*')]
    .filter(e => e.children.length === 0
      && (e.textContent||'').trim().length > 8
      && /%s/.test(e.textContent));
  if (!items.length) return '"no-match"';
  items[0].click();
  return JSON.stringify({clicked: items[0].tagName,
                         txt: items[0].textContent.trim().slice(0, 40)});
})()""" % args.match.replace('"', ''))
        print("点草稿: %s" % raw)
        time.sleep(4.0)
        info = json.loads(cdp.eval(r"""
JSON.stringify({
  title: (document.querySelector('input#title')||{}).value || '',
  abstract: (document.querySelector('#abstractContent')||{}).value || '',
  editorLen: (document.querySelector('#article-editor')||{}).value
              ? document.querySelector('#article-editor').value.length
              : (document.querySelector('[contenteditable=true]')||{textContent:''}).textContent.length,
  community: (function(){
    const lab = [...document.querySelectorAll('label')]
      .find(l => /发布子社区/.test(l.textContent));
    if (!lab) return null;
    const item = lab.closest('.next-form-item');
    return item ? item.textContent.replace(/\s+/g,' ').slice(0, 80) : null;
  })()
})""") or "{}")
        shot(cdp, os.path.join(SHOT_DIR, "yun_al_edit.png"))
        print("title=%r" % info.get("title", "")[:60])
        print("abstract=%r" % info.get("abstract", "")[:60])
        print("editorLen=%s" % info.get("editorLen"))
        print("community=%r" % info.get("community"))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_dialog(args):
    """腾讯云：打开 draftId 编辑器 → 点「去发布」→ dump 发布弹窗字段。
    红线：只开弹窗 dump，绝不点弹窗里的确认发布。"""
    proc, cdp = _launch_profile(args.port)
    try:
        url = ("https://cloud.tencent.com/developer/article/write-new"
               "?draftId=%s" % args.draft)
        cdp.open(url)
        time.sleep(7.0)
        pin_viewport(cdp)
        time.sleep(1.0)
        pos_raw = cdp.eval(r"""
(function(){
  const b = [...document.querySelectorAll('button, [role=button], a, span, div')]
    .filter(e => e.offsetParent !== null
      && (e.textContent||'').trim() === '去发布');
  if (!b.length) return '"no-publish-btn"';
  const r = b[0].getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
})()""")
        print("去发布按钮: %s" % pos_raw)
        pos = json.loads(pos_raw)
        if "x" in pos:
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                      "button": "left", "buttons": 1, "clickCount": 1})
            time.sleep(0.08)
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                      "button": "left", "buttons": 0, "clickCount": 1})
            time.sleep(6.0)
        # 通用弹窗探测
        ovl = cdp.eval(r"""
JSON.stringify([...document.querySelectorAll('[class*=dialog i],[class*=modal i],[class*=popup i],[class*=Dialog i],[class*=Modal i]')]
  .filter(e => e.offsetParent !== null && e.offsetWidth > 200)
  .slice(0, 5).map(e => ({cls: (e.className||'').toString().slice(0,60),
                          w: e.offsetWidth, h: e.offsetHeight,
                          text: e.textContent.replace(/\s+/g,' ').slice(0,200)})))""") or "[]"
        print("浮层: %s" % ovl)
        dlg = json.loads(cdp.eval(r"""
(function(){
  // 全页找弹窗容器：含「摘要」或「一键提取」的浮层
  const dlg = [...document.querySelectorAll('div')]
    .filter(e => /一键提取|文章封面|摘要/.test(e.textContent||'')
      && e.offsetWidth > 300 && e.offsetWidth < 900
      && e.offsetHeight > 200 && e.offsetHeight < 900)
    .sort((a, b) => a.offsetWidth * a.offsetHeight - b.offsetWidth * b.offsetHeight)[0];
  if (!dlg) return '"no-dialog"';
  const named = (sel) => [...dlg.querySelectorAll(sel)].map(e => ({
    tag: e.tagName, ph: e.placeholder || '', val: (e.value||'').slice(0, 40),
    cls: (e.className||'').toString().slice(0, 40)}));
  return JSON.stringify({
    cls: (dlg.className||'').toString().slice(0, 80),
    text: dlg.textContent.replace(/\s+/g, ' ').slice(0, 500),
    inputs: named('input, textarea'),
    btns: [...dlg.querySelectorAll('button, a, [role=button], span')]
      .map(b => (b.textContent||'').trim()).filter(t => t && t.length <= 12)
      .slice(0, 40),
    imgs: [...dlg.querySelectorAll('img')].map(i =>
      (i.src||'').slice(0, 80)).slice(0, 8)
  });
})()""") or "{}")
        if isinstance(dlg, str):
            print("弹窗: %s" % dlg)
        else:
            print("弹窗容器 cls=%r" % dlg.get("cls"))
            print("text: %s" % dlg.get("text", "")[:400])
            print("\n-- inputs --")
            for i in dlg.get("inputs", []):
                print("    <%s> ph=%r val=%r cls=%r" % (i.get("tag"),
                      i.get("ph"), i.get("val"), i.get("cls")))
            print("\n-- buttons --")
            print("    %s" % " | ".join(dlg.get("btns", [])))
            print("\n-- imgs --")
            for s in dlg.get("imgs", []):
                print("    %s" % s)
        shot(cdp, os.path.join(SHOT_DIR, "yun_tc_dialog.png"))
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_tc_extract(args):
    """腾讯云：打开草稿 → 发布抽屉 → 点「一键提取」→ 验证摘要被填。
    红线：不点确认发布；抽屉直接关掉（草稿内容不受影响）。"""
    proc, cdp = _launch_profile(args.port)
    try:
        url = ("https://cloud.tencent.com/developer/article/write-new"
               "?draftId=%s" % args.draft)
        cdp.open(url)
        time.sleep(7.0)
        pin_viewport(cdp)
        time.sleep(1.0)
        pos_raw = cdp.eval(r"""
(function(){
  const b = [...document.querySelectorAll('button, [role=button], a, span, div')]
    .filter(e => e.offsetParent !== null
      && (e.textContent||'').trim() === '去发布');
  if (!b.length) return '"no-publish-btn"';
  const r = b[0].getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
})()""")
        pos = json.loads(pos_raw)
        cdp.send("Input.dispatchMouseEvent",
                 {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                  "button": "left", "buttons": 1, "clickCount": 1})
        time.sleep(0.08)
        cdp.send("Input.dispatchMouseEvent",
                 {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                  "button": "left", "buttons": 0, "clickCount": 1})
        time.sleep(5.0)
        pos2_raw = cdp.eval(r"""
(function(){
  const b = [...document.querySelectorAll('button, a, span, div')]
    .filter(e => e.offsetParent !== null
      && (e.textContent||'').trim() === '一键提取');
  if (!b.length) return '"no-extract-btn"';
  const r = b[0].getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
})()""")
        print("一键提取按钮: %s" % pos2_raw)
        pos2 = json.loads(pos2_raw)
        if "x" in pos2:
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mousePressed", "x": pos2["x"], "y": pos2["y"],
                      "button": "left", "buttons": 1, "clickCount": 1})
            time.sleep(0.08)
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mouseReleased", "x": pos2["x"], "y": pos2["y"],
                      "button": "left", "buttons": 0, "clickCount": 1})
            time.sleep(8.0)                     # AI 提取要几秒
        val = cdp.eval(r"""
(document.querySelector('.editor-publish-drawer__textarea-main')||{}).value || '(空)'""")
        print("摘要值: %r" % str(val)[:200])
        shot(cdp, os.path.join(SHOT_DIR, "yun_tc_extract.png"))
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_al_fields(args):
    """阿里云：载入草稿 → 点AI生成摘要 → 开社区下拉 dump 选项 → dump 图片控件。
    红线：不点发布文章、不点存为草稿。"""
    proc, cdp = _launch_profile(args.port)
    try:
        tid = open_page(cdp, "https://developer.aliyun.com/article/new", settle=6.0)
        pin_viewport(cdp)
        cdp.eval("document.querySelector('.draft-box').click()")
        time.sleep(3.0)
        raw = cdp.eval(r"""
(function(){
  const d = document.querySelector('.next-dialog');
  if (!d) return '"no-dialog"';
  const items = [...d.querySelectorAll('*')]
    .filter(e => e.children.length === 0
      && (e.textContent||'').trim().length > 8
      && /%s/.test(e.textContent));
  if (!items.length) return '"no-match"';
  items[0].click();
  return '"ok"';
})()""" % args.match.replace('"', ''))
        print("载入草稿: %s" % raw)
        time.sleep(4.0)
        # 0) 关掉「免责声明」模态框（每次会话首次交互都会弹，挡一切点击）
        dis = cdp.eval(r"""
(function(){
  const dlg = [...document.querySelectorAll('.next-dialog')]
    .find(d => /免责声明/.test(d.textContent||''));
  if (!dlg) return '"none"';
  const x = dlg.querySelector('.next-dialog-close');
  if (x) { x.click(); return '"closed"'; }
  return '"no-close-btn"';
})()""")
        print("免责声明: %s" % dis)
        time.sleep(1.5)
        # 1) AI 生成摘要（先滚进视口再点，y 出视口 = 点到别处）
        pos_raw = cdp.eval(r"""
(function(){
  const b = document.querySelector('.ai-summary-btn');
  if (!b) return '"no-ai-btn"';
  b.scrollIntoView({block: 'center'});
  const r = b.getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2});
})()""")
        print("AI摘要按钮: %s" % pos_raw)
        pos = json.loads(pos_raw)
        if "x" in pos:
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mousePressed", "x": pos["x"], "y": pos["y"],
                      "button": "left", "buttons": 1, "clickCount": 1})
            time.sleep(0.08)
            cdp.send("Input.dispatchMouseEvent",
                     {"type": "mouseReleased", "x": pos["x"], "y": pos["y"],
                      "button": "left", "buttons": 0, "clickCount": 1})
            time.sleep(15.0)                    # AI 生成要几秒
        abstract = cdp.eval(
            "(document.querySelector('#abstractContent')||{}).value || '(空)'")
        print("AI 摘要: %r" % str(abstract)[:200])
        # 点击后有没有冒出确认框/toast
        print("点击后浮层: %s" % cdp.eval(r"""
JSON.stringify([...document.querySelectorAll('.next-dialog')]
  .filter(d => d.offsetParent !== null).slice(0, 3)
  .map(d => d.textContent.replace(/\s+/g, ' ').slice(0, 80)))"""))
        # 2) 社区下拉：滚进视口→点开→dump 选项
        sel_raw = cdp.eval(r"""
(function(){
  const lab = [...document.querySelectorAll('label')]
    .find(l => /发布子社区/.test(l.textContent));
  if (!lab) return '"no-label"';
  const item = lab.closest('.next-form-item');
  const sel = item.querySelector('.next-select, [class*=select]');
  if (!sel) return '"no-select"';
  sel.scrollIntoView({block: 'center'});
  const r = sel.getBoundingClientRect();
  return JSON.stringify({x: r.x + r.width/2, y: r.y + r.height/2,
                         cls: (sel.className||'').toString().slice(0,50)});
})()""")
        print("社区下拉: %s" % sel_raw)
        try:
            sp = json.loads(sel_raw)
            if "x" in sp:
                cdp.send("Input.dispatchMouseEvent",
                         {"type": "mousePressed", "x": sp["x"], "y": sp["y"],
                          "button": "left", "buttons": 1, "clickCount": 1})
                time.sleep(0.08)
                cdp.send("Input.dispatchMouseEvent",
                         {"type": "mouseReleased", "x": sp["x"], "y": sp["y"],
                          "button": "left", "buttons": 0, "clickCount": 1})
                time.sleep(2.0)
                opts = cdp.eval(r"""
JSON.stringify([...document.querySelectorAll('.next-menu-item')]
  .slice(0, 15).map(e => e.textContent.trim()))""")
                print("社区选项: %s" % opts)
        except Exception as e:                             # noqa: BLE001
            print("下拉失败: %s" % str(e)[:80])
        # 3) 文章图片区控件
        pic = cdp.eval(r"""
(function(){
  const lab = [...document.querySelectorAll('label')]
    .find(l => /文章图片/.test(l.textContent));
  if (!lab) return '"no-pic-label"';
  const item = lab.closest('.next-form-item');
  return JSON.stringify({html: item.innerHTML.slice(0, 800)});
})()""")
        print("图片区: %s" % str(pic)[:600])
        shot(cdp, os.path.join(SHOT_DIR, "yun_al_fields.png"))
        _close(cdp, tid)
    finally:
        try:
            cdp.bws.close()
        except Exception:                                      # noqa: BLE001
            pass
        proc.terminate()


def cmd_open(args):
    """起带登录态的 Chrome 打开 URL 并保持（给人看，不关）。

    ⚠ 沙箱会在宿主 python 退出时回收 Chrome 子进程 → --stay N 让宿主
    挂着定期心跳（Chrome 死了才退），通常配后台任务用。"""
    proc, cdp = _launch_profile(args.port)
    cdp.open(args.url)
    print("已打开: %s" % args.url)
    try:
        cdp.bws.close()
    except Exception:                                          # noqa: BLE001
        pass
    if args.stay > 0:
        print("宿主保持 %d 秒（Chrome 死亡即提前退出）…" % args.stay)
        deadline = time.time() + args.stay
        while time.time() < deadline:
            if proc.poll() is not None:
                print("Chrome 已退出（pid=%d）" % proc.pid)
                return
            time.sleep(5)
    else:
        print("（Chrome pid=%d；注意：宿主退出会连带杀掉它）" % proc.pid)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="起 Chrome 等登录，成功后自动探编辑器")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--wait", type=int, default=600, help="等登录秒数")
    p.add_argument("--keep", action="store_true", help="完成后不关 Chrome")
    p.add_argument("--no-probe", action="store_true", help="跳过编辑器发现")
    p.add_argument("--tencent-draft", default="337119")
    p.add_argument("--aliyun-draft", default="1696290")
    p.set_defaults(fn=cmd_login)

    q = sub.add_parser("cookies", help="dump 两域 cookie 名")
    q.add_argument("--port", type=int, default=PORT)
    q.set_defaults(fn=cmd_cookies)

    p2 = sub.add_parser("al-editor", help="阿里云编辑器深挖（摘要/社区/图片控件）")
    p2.add_argument("--port", type=int, default=PORT)
    p2.add_argument("--draft", default="1696290")
    p2.set_defaults(fn=cmd_al_editor)

    p3 = sub.add_parser("tc-nav", help="腾讯云创作中心抓草稿编辑入口")
    p3.add_argument("--port", type=int, default=PORT)
    p3.set_defaults(fn=cmd_tc_nav)

    p4 = sub.add_parser("al-drafts", help="阿里云点草稿箱抓草稿链接")
    p4.add_argument("--port", type=int, default=PORT)
    p4.set_defaults(fn=cmd_al_drafts)

    p5 = sub.add_parser("tc-deep", help="腾讯云 console 深挖 iframe/链接/按钮")
    p5.add_argument("--port", type=int, default=PORT)
    p5.set_defaults(fn=cmd_tc_deep)

    p6 = sub.add_parser("tc-creator", help="腾讯云社区找创作中心入口")
    p6.add_argument("--port", type=int, default=PORT)
    p6.add_argument("--url", default=TENCENT_HOME)
    p6.set_defaults(fn=cmd_tc_creator)

    p7 = sub.add_parser("al-debug", help="阿里云编辑器调试 dump（草稿箱在哪）")
    p7.add_argument("--port", type=int, default=PORT)
    p7.set_defaults(fn=cmd_al_debug)

    p8 = sub.add_parser("tc-drafts", help="腾讯云草稿箱标签页 dump 草稿卡片")
    p8.add_argument("--port", type=int, default=PORT)
    p8.set_defaults(fn=cmd_tc_drafts)

    p9 = sub.add_parser("tc-edit", help="腾讯云草稿箱点编辑验证草稿载入")
    p9.add_argument("--port", type=int, default=PORT)
    p9.add_argument("--index", type=int, default=0)
    p9.set_defaults(fn=cmd_tc_edit)

    pa = sub.add_parser("al-edit", help="阿里云草稿箱点标题验证草稿载入")
    pa.add_argument("--port", type=int, default=PORT)
    pa.add_argument("--match", default="bootstrapped")
    pa.set_defaults(fn=cmd_al_edit)

    pb = sub.add_parser("tc-dialog", help="腾讯云去发布弹窗字段 dump（不点确认）")
    pb.add_argument("--port", type=int, default=PORT)
    pb.add_argument("--draft", default="337075")
    pb.set_defaults(fn=cmd_tc_dialog)

    pc = sub.add_parser("tc-extract", help="腾讯云一键提取摘要验证")
    pc.add_argument("--port", type=int, default=PORT)
    pc.add_argument("--draft", default="337075")
    pc.set_defaults(fn=cmd_tc_extract)

    pd = sub.add_parser("al-fields", help="阿里云AI摘要+社区下拉+图片控件验证")
    pd.add_argument("--port", type=int, default=PORT)
    pd.add_argument("--match", default="bootstrapped")
    pd.set_defaults(fn=cmd_al_fields)

    pe = sub.add_parser("open", help="起登录态 Chrome 打开 URL 并保持")
    pe.add_argument("--port", type=int, default=PORT)
    pe.add_argument("--url", required=True)
    pe.add_argument("--stay", type=int, default=0,
                    help="宿主保持秒数（防沙箱回收 Chrome），0=立即退出")
    pe.set_defaults(fn=cmd_open)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
