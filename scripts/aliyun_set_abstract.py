#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阿里云**已发布文章**就地改摘要（摘要 = `textarea#abstractContent`）。

入口同 aliyun_retitle：`/article/new/cloudnative?publish=<aid>` 会把已发布
正文 + 标题 + 摘要带回来，改完点「发布文章」即原地更新，URL /article/<aid>
不变。

坑（继承 aliyun_retitle 的实测结论）
------------------------------------
1. 受控字段必须用 native setter + input/change，直赋 value 会被框架覆盖。
2. 不要 `el.blur()` —— 会触发校验导致 Runtime.evaluate 阻塞。
3. 点发布后可能弹**二次滑块**，必须原地等用户拖，不能退出重跑。

用法
----
    python -X utf8 scripts/aliyun_set_abstract.py --aid 1779976 --abstract "..."
    python -X utf8 scripts/aliyun_set_abstract.py --aid 1779976 --abstract "..." --dry
"""
import argparse
import json
import os
import re
import sys
import time

import yun_finalize as F

ABSTRACT_JS = """(()=>{const t=document.querySelector('#abstractContent');
  if(!t) return 'NO_FIELD';
  t.focus();
  const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
  s.call(t, %s);
  t.dispatchEvent(new Event('input',{bubbles:true}));
  t.dispatchEvent(new Event('change',{bubbles:true}));
  return t.value.length;})()"""

READ_JS = """(()=>{const t=document.querySelector('#abstractContent');
  return t?t.value:'NONE';})()"""


def set_abstract(cdp, text):
    return cdp.eval(ABSTRACT_JS % json.dumps(text, ensure_ascii=False))


DIAG_JS = """(()=>{
  const vis = e => !!(e.offsetWidth || e.offsetHeight);
  const btns = [...document.querySelectorAll('button')].filter(vis)
    .map(b=>(b.textContent||'').trim()).filter(t=>t).slice(0,20);
  const dlg = [...document.querySelectorAll('.next-dialog')].filter(vis)
    .map(d=>[...d.querySelectorAll('button')].map(b=>(b.textContent||'').trim()).join(','));
  const cap = (()=>{const e=document.querySelector('.aliyunCaptcha-top');
    if(!e) return ''; const r=e.getBoundingClientRect();
    return Math.round(r.width)+'x'+Math.round(r.height);})();
  return JSON.stringify({href:location.href, btns:btns, dlg:dlg, cap:cap});
})()"""

PUBLISH_FINDER = """
  [].filter.call(document.querySelectorAll('button'),
    function(e){ return (e.textContent||'').trim() === '发布文章'; })[0]"""


def diag(cdp):
    try:
        return json.loads(cdp.eval(DIAG_JS) or "{}")
    except Exception:
        return {}


def press_publish(cdp, captcha_wait):
    """点「发布文章」→ 处理确认框 / 滑块，最多 3 轮。返回 True=已跳转。"""
    for attempt in range(1, 4):
        d = diag(cdp)
        if re.search(r"/article/\d+", d.get("href") or ""):
            return True
        # 1) 有确认弹窗先点掉
        if d.get("dlg"):
            for label in ("继续发布", "确认"):
                pos = F._pos_of(cdp, """
                  [].filter.call(document.querySelectorAll('.next-dialog button'),
                    function(e){ return (e.textContent||'').trim() === %s; })[0]"""
                              % json.dumps(label))
                if isinstance(pos, dict):
                    print("  发布确认框 → %s（弹窗按钮 %s）" % (label, d["dlg"]))
                    F._click_real(cdp, pos)
                    time.sleep(3)
                    break
            continue
        # 2) 有滑块先等人工拖完
        cap = d.get("cap") or ""
        if cap and cap not in ("0x0", ""):
            print("  ⚠ 滑块挡路（%s），等待人工拖动" % cap)
            F._wait_captcha(cdp, captcha_wait, where="（第 %d 轮点击前）" % attempt)
            continue
        # 3) 点「发布文章」。坐标点击前让滚动停稳（页面 scroll-behavior 可能是
        #    smooth：scrollIntoView 之后立刻取坐标会拿到滚动中的旧位置，
        #    点击就落到别的元素上 —— 表现为「点了没反应、也没弹窗」）。
        pos = F._pos_of(cdp, PUBLISH_FINDER)
        if not isinstance(pos, dict):
            print("  页面可见按钮：%s" % "、".join(d.get("btns") or [])[:200])
            raise SystemExit("✗ 没找到「发布文章」按钮")
        print("  [%d] 点「发布文章」@ %s,%s" % (attempt, pos["x"], pos["y"]))
        F._click_real(cdp, pos)
        time.sleep(0.8)
        if attempt >= 2:
            r = cdp.eval("""(()=>{const b=[...document.querySelectorAll('button')]
              .filter(e=>(e.textContent||'').trim()==='发布文章'&&(e.offsetWidth||e.offsetHeight))[0];
              if(!b) return 'NO_BTN'; b.click(); return 'JS_CLICK';})()""")
            print("  [%d] JS click 兜底: %s" % (attempt, r))
        time.sleep(3)
        d2 = diag(cdp)
        print("  点击后：href=%s cap=%s dlg=%s"
              % ((d2.get("href") or "")[-40:], d2.get("cap"), d2.get("dlg")))
        # 点完之后页面常弹一个短命 toast（「请填写…」「标题不能…」），
        # 不抓下来就只能看到「点了没反应」。
        toast = cdp.eval("""(()=>{const ts=[...document.querySelectorAll(
          '.next-message,[class*=toast],[class*=Message],[class*=notice]')]
          .filter(e=>e.offsetWidth||e.offsetHeight).map(e=>(e.innerText||'').trim());
          return ts.join(' | ').slice(0,200);})()""")
        if toast:
            print("  ⚠ 页面提示：%s" % toast)
    return False


DESC_RE = re.compile(r'<div class="article-desc">.*?<span>(.*?)</span>', re.S)
LASTMOD_RE = re.compile(r'name="last-modified" content="([^"]*)"')


def parse_public_desc(html):
    """从公开页 HTML 取（摘要位文本, last-modified）。抽出来是为了能单测。"""
    h = html or ""
    m = DESC_RE.search(h)
    desc = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    lm = LASTMOD_RE.search(h)
    return desc, (lm.group(1) if lm else "")


def verify_public(aid, expect, tries=3, wait=20):
    """回读**公开页**摘要位（`div.article-desc`），这才是「改好了」的判据。

    ⚠ 只证明「控件写入 + 发布接口跳转」是假绿：2026-11-06 实测，
    已发布文章一改就重新进审核，审核期内页面顶部「内容审核中，暂不支持
    编辑」、`article-desc` 与 `meta description` 都还是旧值（`last-modified`
    却已随提交更新）。所以必须回读公开页，未刷新就如实报「待审核」。
    """
    import gzip
    import urllib.request as U
    op = U.build_opener(U.ProxyHandler({}))
    for i in range(tries):
        try:
            req = U.Request(
                "https://developer.aliyun.com/article/%s?t=%d"
                % (aid, int(time.time())),
                headers={"User-Agent": "Mozilla/5.0",
                         "Cache-Control": "no-cache"})
            r = op.open(req, timeout=25)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            h = raw.decode("utf-8", "replace")
        except Exception as e:
            print("  公开页回读失败（%s），%ds 后重试" % (type(e).__name__, wait))
            time.sleep(wait)
            continue
        desc, lastmod = parse_public_desc(h)
        print("  公开页 desc（%d 字）：%s" % (len(desc), desc[:80] or "(空)"))
        print("  last-modified：%s" % (lastmod or "?"))
        if not expect or expect in desc:
            return True
        if i < tries - 1:
            time.sleep(wait)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", required=True)
    ap.add_argument("--abstract", required=True)
    ap.add_argument("--expect-desc", default="",
                    help="公开页摘要应包含的特征串；省略则取摘要里第一个 3 位以上数字")
    ap.add_argument("--no-verify", action="store_true",
                    help="不自动回读公开页（默认回读）")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--captcha-wait", type=int, default=600)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    if len(a.abstract) > 300:
        raise SystemExit("✗ 摘要 %d 字，超阿里云 300 字上限" % len(a.abstract))

    proc, cdp = F._connect(headed=a.headed or True)
    url = "https://developer.aliyun.com/article/new/cloudnative?publish=%s" % a.aid
    try:
        cdp.open(url)
        cdp.ws.sock.settimeout(90)
        cdp.bws.sock.settimeout(90)
        time.sleep(12)
        F._wait_captcha(cdp, a.captcha_wait, where="（开编辑页时）")
        # 确认正文确实带出来了（否则点发布会发出空稿）
        state = F._aliyun_content_state(cdp)
        print("  当前页：标题 %d 字 / 正文 %d 字" % (state["title"], state["body"]))
        if state["title"] == 0 or state["body"] == 0:
            raise SystemExit("✗ 已发布正文没带出来，停手（不点发布）")
        old = cdp.eval(READ_JS)
        print("  旧摘要: %s" % (str(old)[:60] + ("…" if old and len(str(old)) > 60 else "")))
        got = set_abstract(cdp, a.abstract)
        print("  写入后长度: %s" % got)
        if got != len(a.abstract):
            raise SystemExit("✗ 摘要未写入（%s）" % got)
        time.sleep(1)
        now = cdp.eval(READ_JS)
        if now != a.abstract:
            raise SystemExit("✗ 回读不一致，停手")
        if a.dry:
            print("（--dry，未点发布）")
            return 0
        pos = F._pos_of(cdp, PUBLISH_FINDER)
        if not isinstance(pos, dict):
            raise SystemExit("✗ 没找到「发布文章」按钮")
        press_publish(cdp, a.captcha_wait)
        expect = a.expect_desc
        if not expect:
            m3 = re.search(r"\d{3,}", a.abstract)
            expect = m3.group(0) if m3 else ""
        for _ in range(20):
            time.sleep(2)
            cur = cdp.eval("location.href") or ""
            m = re.search(r"/article/(\d+)", cur)
            if m:
                print("  ✅ 已提交: https://developer.aliyun.com/article/%s"
                      % m.group(1))
                if a.no_verify:
                    return 0
                print("  回读公开页确认摘要是否生效…")
                if verify_public(m.group(1), expect):
                    print("  ✅ 公开页摘要已生效（含「%s」）" % expect)
                    return 0
                print("  ⚠ 已提交但公开页摘要仍是旧值 —— 多半进了审核队列"
                      "（页面顶部「内容审核中，暂不支持编辑」）。")
                print("    ⛔ 这不叫「改好了」：审核通过后需再回读一次 "
                      "div.article-desc 才能销账。")
                return 2
        print("  最终 URL:", cdp.eval("location.href"))
        try:
            import base64
            d = (cdp.send("Page.captureScreenshot", {"format": "png"})
                 .get("result") or {}).get("data")
            if d:
                open("tmp/ali_publish_fail.png", "wb").write(base64.b64decode(d))
                print("  现场截图: tmp/ali_publish_fail.png")
        except Exception as e:
            print("  截图失败:", type(e).__name__)
        raise SystemExit("✗ 未见跳转，去页面人工确认")
    finally:
        try:
            cdp.close()
        except Exception:
            pass


if __name__ == "__main__":
    # 零绝对路径依赖：脚本可能被任意 cwd 调用
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
