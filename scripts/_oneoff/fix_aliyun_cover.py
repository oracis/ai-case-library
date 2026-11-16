#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给**已发布**的阿里云文章补「文章首图」（firstImg），修卡片无图。

背景（2026-11-16）
------------------
`publish_aliyun()` 发表时调 `upload_image()` 传首图，supertux 那篇（1783930）
的日志是「列表项出现但没抓到 URL」，但台账写成了「upload_image 返回空」⇒
`cover_readback=empty`、文章卡片无图。对照：minetest 那篇 7.95 MB 的图能传成，
supertux 6.86 MB 失败 ⇒ **不是文件大小问题，是抓 URL 的时机问题**
（列表项先出现、`img.src` 还是空/blob 就读了，一次就放弃）。

`upload_image()` 已改成「轮询 75 秒 + 多策略取址（src / data-src / 背景图）
+ 只认 http(s)」，本脚本是它对**已发布文章**的补丁入口。

流程
----
开 `/article/new/cloudnative?publish=<aid>`（会把已发布正文带回来）→ 校验正文
真的带出来了 → 传首图 → 拿不到 http 地址就**停手不发布**（不许再空发一次）→
点「发布文章」（可能要人工拖滑块）→ 匿名回读公开页确认首图真的在。

⚠ 副作用：改已发布文章会**重回「内容审核中」**，通常几分钟内二次过审，期间
   公开页可能短暂 404 / 显示审核中 —— 这是平台行为，不是这次没改成。

用法::

    python -X utf8 scripts/_oneoff/fix_aliyun_cover.py --aid 1783930 \
        --cover out/toutiao/ext-ep-005-supertux/cover.png \
        --case ext-ep-005-supertux [--go] [--captcha-wait 300]
"""
import argparse
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import aliyun_cover_probe as P  # noqa: E402
import aliyun_set_abstract as A  # noqa: E402
import cover_verify as CV  # noqa: E402
import yun_draft_cdp as Y  # noqa: E402
import yun_finalize as F  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--aid", required=True, help="已发布文章 id（URL 里的那个）")
ap.add_argument("--draft", default="", help="草稿 id；draftDetail 只认这个，缺省按台账反查")
ap.add_argument("--cover", required=True)
ap.add_argument("--case", required=True)
ap.add_argument("--go", action="store_true")
ap.add_argument("--captcha-wait", type=int, default=300)
a = ap.parse_args()

cover = a.cover if os.path.isabs(a.cover) else os.path.join(ROOT, a.cover)
if not os.path.isfile(cover):
    raise SystemExit("✗ 封面文件不存在：%s" % cover)
print("封面: %s（%.2f MB）" % (cover, os.path.getsize(cover) / 1048576.0))

URL_PUB = "https://developer.aliyun.com/article/%s" % a.aid
EDIT = "https://developer.aliyun.com/article/new/cloudnative?publish=%s" % a.aid
ECO_RE = re.compile(r"https://ucc\.alicdn\.com/pic/developer-ecology/[^\"'\s,)]+")


DRAF_DETAIL = """
fetch("/developer/api/articleDraft/draftDetail?aid=%s",
      {credentials: "include", cache: "no-store"})
  .then(function(r){ return r.text(); })
  .catch(function(e){ return "ERR|" + e; })"""


def draft_cover(cdp, draft_id):
    """读 `draftDetail.coverUrl` —— 阿里云「文章首图」唯一可信判据。

    🚨 别去公开页找 `developer-ecology` 图：2026-11-16 实测**已知有首图的
      1780571 / 1783543 公开页里也一张都没有**，`og:image` 恒为平台默认
      200×200 图标 ⇒ 拿公开页判「有没有首图」永远是假的「没有」。
    """
    cdp.open("https://developer.aliyun.com/")
    time.sleep(6)
    r = cdp.eval(DRAF_DETAIL % draft_id)
    try:
        j = json.loads(str(r))
    except (ValueError, TypeError):
        return {"ok": False, "cover": "", "raw": str(r)[:200]}
    d = j.get("data") or {}
    return {"ok": bool(j.get("success")),
            "cover": str(d.get("coverUrl") or ""),
            "title": str(d.get("title") or ""),
            "abstract_len": len(str(d.get("abstractContent") or "")),
            "content_len": len(str(d.get("content") or "")),
            "modified": str(d.get("gmtModified") or ""),
            "raw": ""}


# 🚨 2026-11-16：这里原来有 `page_covers()`（读编辑页「文章图片」列表）和
#   `public_probe()`（匿名读公开页找首图）两个判据，**两个都是假的**：
#     · 编辑页列表是页面态，重载即空 —— 会把「已保存」判成「没保存」；
#     · 公开页压根没有首图位（og:image 恒为平台默认图标）。
#   真判据是个人主页卡片的 `div.dynamic-image`，见 `scripts/aliyun_cover_probe.py`。
#   别再往这个文件里加第三个 DOM 判据。


def publish_with_diag(cdp, captcha_wait):
    """点发布 → 处理确认框/滑块，最多 8 轮，每轮打印弹窗正文与按钮可点状态。

    A.press_publish 只打按钮文字、且 3 轮就放弃；2026-11-16 实测它把
    「确认」点了两次仍未跳转，看不出是被禁用、还是叠了两层弹窗。
    这里把 `.next-dialog` 的 innerText 和每个按钮的 `disabled` 都打出来。
    """
    for i in range(1, 9):
        d = diag2(cdp)
        href = d.get("href") or ""
        if re.search(r"/article/\d+", href):
            print("  ✓ 已跳转: %s" % href[-40:])
            return True
        dls = d.get("dialogs") or []
        if dls:
            for k, dl in enumerate(dls):
                print("  弹窗[%d] 正文: %s" % (k, (dl.get("text") or "").replace("\n", " / ")[:150]))
                print("  弹窗[%d] 按钮: %s" % (k, dl.get("btns")))
            picked = False
            for label in ("确认", "继续发布", "确定", "发布"):
                pos = F._pos_of(cdp, """
                  [].filter.call(document.querySelectorAll('.next-dialog button'),
                    function(e){ return (e.textContent||'').trim() === %s
                      && !e.disabled && (e.offsetWidth||e.offsetHeight); })[0]"""
                                  % json.dumps(label))
                if isinstance(pos, dict):
                    print("  [%d] 点弹窗按钮「%s」@ %s,%s" % (i, label, pos["x"], pos["y"]))
                    F._click_real(cdp, pos)
                    time.sleep(2)
                    # 🚨 只点一次，然后长等（2026-11-16）：这个页面带 6.5 MB
                    #   首图，点确认后服务端要处理一会儿；原来每 3 秒看一眼
                    #   就再点一次，等于把提交反复打断，弹窗永远关不掉。
                    for k in range(60):
                        time.sleep(2)
                        d2 = diag2(cdp)
                        if re.search(r"/article/\d+", d2.get("href") or ""):
                            print("  ✓ 已跳转: %s" % (d2.get("href") or "")[-40:])
                            return True
                        if d2.get("toast"):
                            print("  [%ds] 页面提示: %s" % (k * 2, d2["toast"]))
                        cap2 = d2.get("cap") or ""
                        if cap2 and cap2 not in ("0x0", ""):
                            print("  ⚠ 滑块挡路（%s），等人工拖动" % cap2)
                            F._wait_captcha(cdp, captcha_wait, where="（点确认后）")
                            # 🚨 滑块过后**只等、绝不再点发布**：2026-11-16 实测
                            #   滑块通过后页面显示「文章发布中」，原逻辑转回外圈
                            #   又点了一次「发布文章」，用没有首图的 payload 把
                            #   第一次提交覆盖掉（编辑页至今无图就是这个原因）。
                            print("  ✓ 滑块已通过，等提交（最多 180 秒，不再点）")
                            for _ in range(90):
                                time.sleep(2)
                                d3 = diag2(cdp)
                                if re.search(r"/article/\d+", d3.get("href") or ""):
                                    print("  ✓ 已跳转: %s"
                                          % (d3.get("href") or "")[-40:])
                                    return True
                                if d3.get("toast"):
                                    print("  [提交中] %s" % d3["toast"])
                            print("  ⚠ 滑块已过但 180 秒仍未跳转")
                            return False
                    picked = True
                    break
            if picked:
                continue
            print("  ⚠ 弹窗里的按钮都不可点（可能还在校验/上传中），等 8 秒再看")
            time.sleep(8)
            continue
        cap = d.get("cap") or ""
        if cap and cap not in ("0x0", ""):
            print("  ⚠ 滑块挡路（%s），等人工拖动" % cap)
            F._wait_captcha(cdp, captcha_wait, where="（第 %d 轮点击前）" % i)
            continue
        if d.get("toast"):
            print("  页面提示: %s" % d["toast"])
        pos = F._pos_of(cdp, A.PUBLISH_FINDER)
        if not isinstance(pos, dict):
            btns = "、".join((d.get("btns") or [])
                             if isinstance(d.get("btns"), list) else [])
            print("  可见按钮: %s" % btns[:150])
            raise SystemExit("✗ 没找到「发布文章」按钮")
        print("  [%d] 点「发布文章」@ %s,%s" % (i, pos["x"], pos["y"]))
        F._click_real(cdp, pos)
        time.sleep(4)
    return False


# 🚨 判据只认 **个人主页动态卡片的 `div.dynamic-image`**（见
#   `scripts/aliyun_cover_probe.py`）。2026-11-16 实测：
#     · 1783930 有该节点，src=.../32o2bc7dqh5k6_3bd6080be4a44a758a89d943bbd85c4f.jpg
#     · 对照 1783731 根本没有该节点 ⇒ 节点存在与否就是「有没有封面」
#   其它位置全部证伪，别再拿它们当判据：
#     · 公开页：无 coverUrl/firstImg 字段，og:image 恒为平台默认图标
#     · `draftDetail.coverUrl`：草稿被消费后对已发布文章读不到
#     · 编辑页「文章图片」列表：页面态，重载即空（≠ 没保存）
#     · 传图只 PUT 到 OSS 预签名地址，不调用任何落库接口
proc, cdp = Y._connect()
print("9228:", "复用常驻" if proc is None else "本次新起")
cdp.open(EDIT)
time.sleep(9)
F._close_disclaimer(cdp)

pre = P.probe(a.aid)
print("主页卡片: %s" % (("有封面 %s" % pre["cover_url"]) if pre["state"] == "ok"
                       else ("无封面" if pre["state"] == "empty"
                             else "列表里没找到（多半在审核）")))
if pre["state"] == "ok":
    print("⇒ 该文章已有封面，不用补")
    CV.record(a.case, "aliyun", "ok", remote_id=a.aid, source="independent",
              evidence="个人主页卡片 div.dynamic-image=%s" % pre["cover_url"])
    sys.exit(0)

# 🚨 硬闸（2026-11-16 踩出来的）：文章只要在审核队列里，编辑页顶部就挂
#   「内容审核中，暂不支持编辑」，此时**传图 / 点发布都会被静默拒绝**
#   （页面照常回填正文、上传列表还能出图，但保存不进去；表现为「改了半天
#   编辑页还是没有首图」）。而且每次点发布都会让它重新进审核 ⇒ 恶性循环。
#   所以先看一眼这条横幅，有就立刻退出，一次操作都不做。
LOCKED_JS = """(function(){
  var t = document.body ? (document.body.innerText || '') : '';
  return /内容审核中[，,]?\\s*暂不支持编辑/.test(t) ? 'LOCKED' : 'EDITABLE';
})()"""
lock_state = str(cdp.eval(LOCKED_JS) or "")
print("编辑页状态: %s" % lock_state)
if lock_state != "EDITABLE":
    print("✗ 文章在审核队列里、平台锁了编辑 ⇒ 现在做什么都不会落库。")
    print("  等公开页/编辑页解除「内容审核中」后再跑本脚本（不用改任何参数）。")
    sys.exit(2)



# 正文必须带出来，否则一点发布会把已发布文章清空
body_len = cdp.eval(
    "(function(){var e=document.querySelector('.mditor textarea.textarea');"
    "return e?e.value.length:-1;})()")
title_v = cdp.eval(
    "(function(){var e=document.querySelector('#title');return e?e.value:'';})()")
print("带出正文 %s 字 ｜ 标题: %r" % (body_len, str(title_v)[:60]))
if not isinstance(body_len, int) or body_len < 200:
    raise SystemExit("✗ 已发布正文没带出来（%s 字），停手不发布" % body_len)

if not a.go:
    print("\n（干跑）加 --go 才传图并重新发布。")
    sys.exit(0)

u = F.upload_image(cdp, cover)
print("上传结果: %s" % (u or "(空)"))
if not (u and str(u).startswith("http")):
    CV.record(a.case, "aliyun", "empty", remote_id=a.aid, source="independent",
              evidence="补首图失败：上传页拿不到 http 地址（%s）⇒ 未重新发布"
                       % (u or "空"))
    raise SystemExit("✗ 没拿到 http 地址，停手（不点发布，避免再次空发）")

F.ledger_set("__community__%s" % a.aid, uploaded_img=u)
print("✓ 页面已拿到首图地址: %s" % u[:100])

_published = publish_with_diag(cdp, a.captcha_wait)
# 2026-11-16：这里原来挂了一个「抓发布请求体里有没有 firstImg」的钩子。
# 实测两次都抓不到（发布请求不放 fetch/XHR，或字段名不是这个），而**封面
# 到底有没有上去**现在由主页卡片探针直接判定 —— payload 长什么样已无关紧要，
# 别再为它加钩子。
if not _published:
    CV.record(a.case, "aliyun", "unknown", remote_id=a.aid, source="independent",
              evidence="首图已上传（%s）但发布未确认跳转" % u[:120])
    raise SystemExit("✗ 发布没跳转（可能滑块没拖 / 有校验提示），首图地址已记台账，"
                     "确认后重跑本脚本")

# 发布后会重新进审核，卡片要等审核通过才刷新 —— 轮询到有图或超时为止。
cover_url = ""
for i in range(1, 10):
    time.sleep(30 if i > 1 else 20)
    r = P.probe(a.aid, wait=12)
    print("  第 %d 次回读主页卡片: %s" % (i, r["state"]))
    if r["state"] == "ok":
        cover_url = r["cover_url"]
        break

if cover_url:
    print("✓ 封面已上线：%s" % cover_url)
    CV.record(a.case, "aliyun", "ok", remote_id=a.aid, source="independent",
              evidence="补首图成功（已发布文章 %s）个人主页卡片 "
                       "div.dynamic-image=%s" % (a.aid, cover_url))
    print("✓ cover_readback 已记 ok")
else:
    # 刚重新提交、还在审核 ⇒ 卡片可能还没刷新，别急着写 empty
    CV.record(a.case, "aliyun", "unknown", remote_id=a.aid, source="independent",
              evidence="已提交并上传首图（%s），但主页卡片尚未出现 "
                       "div.dynamic-image（多半仍在审核）；等审核过后重跑 "
                       "`scripts/aliyun_cover_probe.py --aid %s` 复核"
                       % (u[:120], a.aid))
    print("⏳ 已提交，卡片暂未刷新。审核过后跑这条复核：")
    print("   python -X utf8 scripts/aliyun_cover_probe.py --aid %s" % a.aid)
