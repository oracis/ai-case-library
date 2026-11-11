# -*- coding: utf-8 -*-
"""小红书「未通过」笔记就地改写 + 重新发布（speel-co，PRD #101 后续）。

背景
----
11-04 发布的《Speel：商品图秒变投放广告片》被判「外部平台信息或联系方式」
（文本违规 + 图片违规）。11-09 已在平台专属覆盖层重写标题/正文/标签/卡片，
并重建 5 张 PNG（out/xhs/speel-co/card-*.png）。

本脚本走平台「修改笔记」入口 `publish/update?id=<noteId>&noteType=normal`，
在**同一篇笔记**上替换标题、正文、全部图片后重新发布 —— 不新建、不删除笔记。
发布会让该笔记重新进审核，属用户 11-09 明确授权的覆盖操作。

用法
----
    python -X utf8 tmp/xhs_rewrite_publish.py          # 干跑：只读校验 + 打印差异
    python -X utf8 tmp/xhs_rewrite_publish.py --go     # 真跑：改稿 + 发布 + 双回读

⚠ 起浏览器 + 改稿 + 发布 + 回读必须在这**一条前台命令**内完成（本机后台
   Chrome 会被整组回收）。
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

import xhs_publish as X           # noqa: E402
import wechat_publish as wp       # noqa: E402
import chrome_cdp_launch as L     # noqa: E402

CASE = "speel-co"
NOTE_ID = "6aeabdf4000000001e0166b6"
UPDATE_URL = ("https://creator.xiaohongshu.com/publish/update?id=%s"
              "&noteType=normal" % NOTE_ID)
PACK = os.path.join(ROOT, "out", "xhs", CASE)
OLD_TITLE = "Speel：商品图秒变投放广告片"
OLD_IMG_MARK = "1040g0k0326bic"       # 旧卡片图在 xhscdn spectrum 上的共同前缀
BAN = ("Speel.co", "Meta", "TikTok", "Reels", "投放", "广告")


def load_pack():
    d = json.load(open(os.path.join(PACK, "note.json"), encoding="utf-8"))
    body = (d.get("body") or "").rstrip()
    tags = d.get("tags") or []
    if tags and not body.endswith(tags[-1]):
        body += "\n" + " ".join(tags)
    imgs = d.get("images") or []
    for fn in imgs:
        p = os.path.join(PACK, fn)
        if not os.path.isfile(p):
            raise SystemExit("缺图片产物：%s" % p)
    bad = [w for w in BAN if w in d["title"] or w in body]
    if bad:
        raise SystemExit("本地改写稿仍命中站外/投放词：%s" % bad)
    return d["title"], body, imgs


def connect(url=UPDATE_URL, wait=9):
    wp._autostart_chrome_if_needed(X.PUB_PORT, profile=L.PLAT_PROFILE)
    sub = X._draft_cdp(fresh=True, url=url)
    time.sleep(wait)
    return sub


def snapshot(sub):
    js = """(function(){
      var ti=document.querySelector("%s");
      var ed=document.querySelector("%s");
      var host=document.querySelector('xhs-publish-btn');
      return JSON.stringify({
        url: location.href,
        title: ti ? ti.value : '',
        body_len: ed ? (ed.innerText||'').length : 0,
        body_head: ed ? (ed.innerText||'').slice(0,90) : '',
        imgs: document.querySelectorAll('.img-list img').length,
        publish: host ? {submit:host.getAttribute('submit-text'),
                         disabled:host.getAttribute('submit-disabled')} : null
      });
    })()""" % (X.SEL_TITLE, X.SEL_BODY)
    return json.loads(sub.eval(js, refresh_context=True) or "{}")


def img_count(sub):
    return int(sub.eval("document.querySelectorAll('%s').length" % X.SEL_IMG,
                        refresh_context=True) or 0)


def img_srcs(sub):
    js = ("JSON.stringify(Array.from(document.querySelectorAll('%s'))"
          ".map(function(e){return e.getAttribute('src')||'';}))" % X.SEL_IMG)
    return json.loads(sub.eval(js, refresh_context=True) or "[]")


def upload_one(sub, fn, idx, total):
    p = os.path.abspath(os.path.join(PACK, fn))
    q = sub.send("DOM.getDocument", {"depth": 0})
    root = (q.get("result") or {}).get("root", {}).get("nodeId")
    n = sub.send("DOM.querySelector", {"nodeId": root, "selector": X.SEL_FILE})
    nid = (n.get("result") or {}).get("nodeId")
    if not nid:
        n = sub.send("DOM.querySelector",
                     {"nodeId": root, "selector": X.SEL_FILE_ANY})
        nid = (n.get("result") or {}).get("nodeId")
    if not nid:
        raise RuntimeError("找不到图片 file input")
    before = img_count(sub)
    sub.send("DOM.setFileInputFiles", {"nodeId": nid, "files": [p]})
    got = X._wait(sub, "document.querySelectorAll('%s').length" % X.SEL_IMG,
                  lambda v, b=before: int(v or 0) > b, tries=45, gap=1.0)
    print("  上传 %d/%d %s → 列表 %s 张" % (idx, total, fn, got))
    if got is None:
        raise RuntimeError("第 %d 张图上传未被识别" % idx)


def remove_one_old(sub):
    """按旧图 URL 前缀定位它所在卡片的关闭按钮（避免误删新图）。"""
    js = """(function(){
      var imgs=Array.from(document.querySelectorAll('__SEL__'));
      var old=imgs.filter(function(e){
        return (e.getAttribute('src')||'').indexOf('__MARK__')>=0;})[0];
      if(!old) return 'no-old';
      var p=old;
      for(var i=0;i<6 && p;i++){
        var b=p.querySelector('.close-btn');
        if(b){ b.click(); return 'clicked'; }
        p=p.parentElement;
      }
      return 'no-btn';
    })()""".replace("__SEL__", X.SEL_IMG).replace("__MARK__", OLD_IMG_MARK)
    return sub.eval(js, refresh_context=True)


def replace_images(sub, files):
    """平台强制至少保留 1 张图：先传一张新图保底 → 删光旧图 → 补齐其余。

    实测（11-09）：`.title-clear-all` 的 JS click 无效，逐张 `.close-btn` 删到
    最后 1 张就删不动了（小红书不允许 0 图）。所以改成「先加后删」。
    最终必须 = len(files) 张且不含旧图 URL 前缀，否则直接放弃发布。
    """
    print("  起始图片:", img_count(sub), "张")
    upload_one(sub, files[0], 1, len(files))
    for i in range(12):
        stale = sum(1 for s in img_srcs(sub) if OLD_IMG_MARK in s)
        if stale == 0:
            break
        r = remove_one_old(sub)
        time.sleep(1.2)
        now = sum(1 for s in img_srcs(sub) if OLD_IMG_MARK in s)
        print("  删旧图 %d: %s → 旧图残留 %d 张" % (i + 1, r, now))
        if now >= stale:
            sub.eval("(function(){var b=document.querySelector('.close-btn.hoverShow');"
                     "if(b)b.click();return 'ok';})()", refresh_context=True)
            time.sleep(1.2)
            now = sum(1 for s in img_srcs(sub) if OLD_IMG_MARK in s)
            if now >= stale:
                raise RuntimeError("旧图删不动（平台限制），放弃发布")
    for i, fn in enumerate(files[1:], 2):
        upload_one(sub, fn, i, len(files))
    n = img_count(sub)
    stale = [s for s in img_srcs(sub) if OLD_IMG_MARK in s]
    print("  最终 %d 张，旧图残留 %d 张" % (n, len(stale)))
    if n != len(files) or stale:
        raise RuntimeError("图片替换不完整：%d 张 / 残留 %d" % (n, len(stale)))


def set_body(sub, body):
    """全选旧正文 → 第一次 insertText 直接替换选区 → 逐行补齐。"""
    lines = body.split("\n")
    js = """(function(){
      var ed=document.querySelector("%s");
      if(!ed) return JSON.stringify({ok:false,why:'no_editor'});
      ed.focus();
      var sel=window.getSelection(), r=document.createRange();
      r.selectNodeContents(ed); sel.removeAllRanges(); sel.addRange(r);
      var lines=%s, ok=0;
      for(var i=0;i<lines.length;i++){
        try{ document.execCommand('insertText', false,
               lines[i]+(i<lines.length-1 ? '\\n' : '')); ok++; }catch(e){}
      }
      ed.dispatchEvent(new Event('input',{bubbles:true}));
      return JSON.stringify({ok:true,lines:lines.length,inserted:ok,
                             len:(ed.innerText||'').length,
                             head:(ed.innerText||'').slice(0,80)});
    })()""" % (X.SEL_BODY, json.dumps(lines, ensure_ascii=False))
    return json.loads(sub.eval(js, refresh_context=True) or "{}")


def verify(sub, title, body):
    print("\n=== 独立回读 1：笔记管理列表 ===")
    sub.send("Page.navigate", {"url": X.NOTE_MANAGER_URL})
    time.sleep(9)
    t = sub.eval("document.body.innerText", refresh_context=True) or ""
    print("  新标题命中 :", title in t)
    print("  旧标题残留 :", OLD_TITLE in t)
    print("  「审核中」 :", "审核中" in t)
    print("  修改建议残留:", "查看修改建议" in t)
    i = t.find(title)
    print("  列表片段   :", repr(t[max(0, i - 40):i + 120]) if i >= 0 else "(未找到)")

    print("\n=== 独立回读 2：笔记编辑页正文 ===")
    sub.send("Page.navigate", {"url": UPDATE_URL})
    time.sleep(9)
    s = snapshot(sub)
    print("  标题       :", s.get("title"))
    print("  正文长度   :", s.get("body_len"))
    print("  正文首句   :", repr(s.get("body_head")))
    print("  图片张数   :", s.get("imgs"))
    full = sub.eval("(function(){var e=document.querySelector(%s);"
                    "return e?e.innerText:'';})()" % json.dumps(X.SEL_BODY),
                    refresh_context=True) or ""
    print("  新正文首句命中:", body.split("\n")[0][:14] in full)
    print("  旧文首句消失  :", "一个人 + 现成的上游" not in full)
    bad = [w for w in BAN if w in full or w in (s.get("title") or "")]
    print("  站外/投放词命中:", bad or "无")
    return {
        "list_title": title in t,
        "old_title_gone": OLD_TITLE not in t,
        "audit_gone": "查看修改建议" not in t,
        "title": s.get("title"),
        "body_ok": body.split("\n")[0][:14] in full,
        "old_body_gone": "一个人 + 现成的上游" not in full,
        "imgs": s.get("imgs"),
        "ban": bad,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="真跑（缺省干跑）")
    a = ap.parse_args()

    title, body, imgs = load_pack()
    print("=== 本地改写稿 ===")
    print("  标题:", title)
    print("  正文:", len(body), "字 /", len(body.split("\n")), "行")
    print("  图片:", imgs)
    print("  站外/投放词:", [w for w in BAN if w in title or w in body] or "零命中")

    sub = connect()
    before = snapshot(sub)
    print("\n=== 平台侧改前快照 ===")
    print(json.dumps(before, ensure_ascii=False, indent=1))
    if NOTE_ID not in (before.get("url") or ""):
        raise SystemExit("✗ 不是目标笔记编辑页，放弃")
    if (before.get("title") or "").strip() != OLD_TITLE:
        raise SystemExit("✗ 标题不是旧稿《%s》，放弃。实际=%r"
                         % (OLD_TITLE, before.get("title")))
    if not a.go:
        print("\n[干跑] 对象核对通过。加 --go 才执行覆盖与发布。")
        return

    print("\n=== 1) 替换 5 张卡片图（先传一张保底，再精准删旧图） ===")
    replace_images(sub, imgs)

    print("\n=== 2) 覆盖标题 / 正文 ===")
    print("  标题:", sub.eval(X._js_fill_title(title), refresh_context=True))
    r = set_body(sub, body)
    print("  正文:", json.dumps(r, ensure_ascii=False))
    if not r.get("ok") or (r.get("len") or 0) < len(body) * 0.8:
        raise SystemExit("✗ 正文替换不完整，放弃发布")
    time.sleep(2)
    mid = snapshot(sub)
    print("  改后快照:", json.dumps(mid, ensure_ascii=False))
    if mid.get("title") != title or mid.get("imgs") != len(imgs):
        raise SystemExit("✗ 改后标题/图片数不符，放弃发布")
    if any(w in (mid.get("body_head") or "") for w in BAN):
        raise SystemExit("✗ 编辑器正文仍含站外词，放弃发布")

    print("\n=== 3) 点「发布」 ===")
    strategy, clicked = X._click_publish(sub)
    print("  策略=%s clicked=%s" % (strategy, clicked))
    if not clicked:
        raise SystemExit("✗ 发布按钮没点到，未提交")
    time.sleep(6)
    print("  提交后 URL:", sub.eval("location.href", refresh_context=True))
    print("  提交后页面提示:", repr(
        (sub.eval("document.body.innerText", refresh_context=True) or "")[:220]))

    print("\n=== 4) 独立双回读 ===")
    res = verify(sub, title, body)
    ok = (res["list_title"] and res["old_title_gone"] and res["audit_gone"]
          and res["title"] == title and res["body_ok"]
          and res["old_body_gone"] and res["imgs"] == len(imgs)
          and not res["ban"])
    print("\n=== 汇总 ===")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print("ALL_CHECKS_PASS" if ok else "SOME_CHECKS_FAILED")
    print("⚠ 无论脚本自报什么，最终判据以平台侧列表 + 编辑页正文回读为准；"
          "重新发布会让笔记进入审核，不写 published 台账。")
    try:
        sub.bws.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
