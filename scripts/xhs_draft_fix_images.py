#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给小红书草稿箱里「图片全裂」的草稿换图 —— 先传本地卡片图，再删裂图。

⚠ 2026-10-20 下午的翻案（排了 16 个探针，先读再动手）
--------------------------------------------------
「草稿图全裂」的主因**不是**签名过期，也**不是**图片丢了：

1. 草稿记录在浏览器本地 IndexedDB `draft-database-v1`（image-draft store），
   fileId / 签名 URL 全部完好，Python 直取全部 200。
2. 编辑器显示裂图的根因：CDN（腾讯 COS）把 spectrum 预览的 Content-Type
   标成 `application/octet-stream`，Chrome ORB 对跨域 no-cors 图片响应
   按 Content-Type 拦截（net::ERR_BLOCKED_BY_ORB）→ naturalWidth=0。
   页内 cors fetch 更惨：CDN 对带 Origin 的请求直接 400（Vary: Origin）。
   ⇒ **显示层问题，客户端无解**；重传解决不了——传完保存、重开照样裂。
3. 本脚本的重传+删图+显式暂存链路本身是通的（当天已真机跑通并验证保存
   落地），只是「修显示」这个目标它达不到。要看图/发布：用
   `xhs_draft_view.py`（data-URL 注入显示，不改数据）；发布不受影响
   （走 fileId，服务端有文件）。

真机踩出来的规矩（改代码前先看这儿，改错就是白跑一小时）
--------------------------------------------------------
1. **先传后删**。实测：编辑器里**只剩最后一张**时，点 `.close-btn` 是删不掉的
   （hover 正常、`elementFromPoint` 命中的就是按钮、点了没反应，连试三条草稿都一样）。
   只要还留着别的图，删除就好使 ⇒ 换图顺序必须是**上传在前、删除在后**，
   而且永远别让图片数量归零。
2. **删图必须 hover 先看Button** `.close-btn` 平时 `visibility:hidden`
   （class 就叫 `hoverShow`），不 hover 时 `elementFromPoint` 命中的是外层占位
   节点 `DIV.feature-guide-anchor`，真点下去落在祖先身上，等于没点。
   另外删完一张后剩余的图会左移，可能正好移到鼠标原来待的位置 —— CDP 的
   mouseMoved 到「同一个坐标」不触发 hover ⇒ 每轮先把鼠标甩到角落。
3. **保存必须显式点「暂存离开」**（`<xhs-publish-btn>` closed shadow DOM 里
   的白色按钮，用 `xp.shadow_publish_btn(sub, cls="white")`），且**改完等
   SETTLE_EDIT 再点**：
   - 导航离开触发的「自动暂存」会把改动丢掉（上传中的图整批丢）；
   - 删完 3s 内就保存，保存快照的还是旧状态，改动整体回滚（2026-10-20 实测）。
   另：保存进记录的新图，编辑器重开后**还是显示裂图**（ORB），别拿
   「重开还裂」判断保存失败——用 IndexedDB 记录或发布结果验收。
4. 预览图上传完成后 src **永远停在 blob:**（网络层 200、CDN 已能取到图），
   「等 src 变正式地址」的信号不存在，只能固定等待。
5. 编辑页没有「保存草稿」按钮；上面第 3 条的「暂存离开」就是保存。

⚠ 合成 click（`jclick`）在小红书时灵时不灵，抽屉入口必须走**真实鼠标事件**。
⚠ 必须**新开标签页**：复用一个开久了的 publish 页会「连得上、点了没反应」。

用法
----
    python -u -X utf8 scripts/xhs_draft_fix_images.py --dry      # 只体检，不动手
    python -u -X utf8 scripts/xhs_draft_fix_images.py --limit 1  # 先试点一条
    python -u -X utf8 scripts/xhs_draft_fix_images.py            # 全量修
    python -u -X utf8 scripts/xhs_draft_fix_images.py --only MagicSlides
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import wechat_publish as wp      # noqa: E402
import xhs_publish as xp         # noqa: E402
from xhs_publish import JS_CLICK_CHAIN, JS_CARDS  # noqa: E402

OUT = xp.OUT
MAX_IMAGES = 18          # 小红书图文上限（编辑器里显示 「n/18」）
SETTLE_UPLOAD = 20       # 传完图等服务端收尾（实测预览的 blob: 永远不换正式地址，
                         #  没有更准的信号，只能固定等）
SETTLE_EDIT = 20         # 删图/改完到保存之间必须隔这么久 —— 2026-10-20 探针实测：
                         #  删完 3s 内保存，保存快照的是旧状态，改动整体回滚；
                         #  等 20s 再显式暂存，改动稳稳落进草稿

# 图片区 DOM 契约（2026-10-20 实测）：
#   .img-container            —— 一张图一个，内含
#     .format-img > img.preview（预览图；裂图 naturalWidth===0）
#     .mask.hover-mask > .close-btn.hoverShow  ← 删这张图（hover 才显形）
JS_IMG_INFO = """(function(){
  var items=[].slice.call(document.querySelectorAll('.img-container'));
  return JSON.stringify(items.map(function(it){
    var r=it.getBoundingClientRect();
    var cb=it.querySelector('.close-btn');
    var cr=cb?cb.getBoundingClientRect():null;
    var im=it.querySelector('img.preview');
    return {src:im?(im.src||''):'', x:Math.round(r.x+r.width/2),
            y:Math.round(r.y+r.height/2),
            cx:cr?Math.round(cr.x+cr.width/2):0,
            cy:cr?Math.round(cr.y+cr.height/2):0,
            nw:im?im.naturalWidth:-1};}));
})()"""

JS_CLOSE_AT = """(function(i){
  var its=[].slice.call(document.querySelectorAll('.img-container'));
  var it=its[i]||its[0];
  if(!it) return null;
  var cb=it.querySelector('.close-btn');
  if(!cb) return null;
  var cr=cb.getBoundingClientRect();
  var cx=Math.round(cr.x+cr.width/2), cy=Math.round(cr.y+cr.height/2);
  var at=document.elementFromPoint(cx,cy);
  return JSON.stringify({cx:cx, cy:cy, vis:getComputedStyle(cb).visibility,
    at: at ? at.tagName+'.'+String(at.className).slice(0,36) : 'none',
    onBtn: !!at && (cb===at || cb.contains(at))});})"""

# 编辑器的卡片「编辑」按钮（页面结构常年是这个三件套）
JS_OPEN_EDIT = "(function(){%s\n" % JS_CLICK_CHAIN + """
  var cards=[].slice.call(document.querySelectorAll('*')).filter(function(x){
    var t=(x.innerText||'');
    return t.indexOf('保存于')>=0 && t.indexOf('编辑')>=0 && t.indexOf('删除')>=0;});
  cards=cards.filter(function(c){
    return !cards.some(function(o){ return o!==c && c.contains(o); });});
  for (var i=0;i<cards.length;i++){
    var c=cards[i];
    if ((c.innerText||'').indexOf(%s)<0) continue;
    var btns=[].slice.call(c.querySelectorAll('*')).filter(function(b){
      return (b.innerText||'').trim()==='编辑' && b.getBoundingClientRect().width>0;});
    if(!btns.length) return 'no-edit-btn';
    jclick(btns[btns.length-1]); return 'clicked';}
  return 'notfound';})()"""

SEL_TITLE = "input.d-text[placeholder*='标题']"
SEL_FILE_IMG = 'input[type=file][accept*="png"]'


# ------------------------------------------------------------------ 纯函数
def need_repair(total, broken, force=False):
    """这条草稿要不要修？

    只有「有图且有裂图」才修。本来就没图的草稿不归本脚本管（那是创作问题，
    不是签名过期），除非 --force。
    """
    if total <= 0:
        return False
    return bool(force) or broken > 0


def resolve_local(case_dir, images):
    """note.json 里的文件名 → 绝对路径。返回 (可用列表, 缺的列表)。

    缺一张就别硬传 —— 传一半的草稿照样发不出去。
    """
    ok, miss = [], []
    for fn in images or []:
        p = os.path.abspath(os.path.join(case_dir, fn))
        (ok if os.path.isfile(p) else miss).append(p if os.path.isfile(p) else fn)
    return ok, miss


def plan_repair(rows):
    """每条草稿怎么处理。rows=[{total, broken, local, force}]。

    action：skip（图都好着）/ repair（要换图）/ blocked（要换但本地没图）
    —— 「修不了」和「不用修」必须分开，都叫 skip 会看不见问题。
    """
    out = []
    for r in rows:
        total = r.get("total", 0)
        broken = r.get("broken", 0)
        local = r.get("local") or []
        act, reason = "skip", "图片正常（%d 张）" % total
        if not local:
            act = "blocked"
            reason = "缺本地图（认不出案例或卡片没生成）"
        elif broken > 0:
            act = "repair"
            reason = "%d/%d 张裂图，本地 %d 张待传" % (broken, total, len(local))
        elif total == 0:
            # 图被删光了（含本脚本上一轮失手的情况）——补回去，别留着空草稿
            act = "repair"
            reason = "草稿里一张图都没有，补 %d 张本地图" % len(local)
        elif total != len(local) and not r.get("force", False):
            act = "skip"
            reason = "%d 张图都正常（本地 %d 张，不动它）" % (total, len(local))
        elif r.get("force", False):
            act = "repair"
            reason = "--force：重传 %d 张" % len(local)
        out.append(dict(r, action=act, reason=reason))
    return out


def broken_srcs(info):
    """体检时**先记住裂图的 src**。

    ⚠ 别在传完图之后再靠 naturalWidth 认裂图：刚传上去的图在处理时也是
      naturalWidth===0（2026-10-20 Podawaa 就是这么被删到 0 张的）。
      动手前先把「哪些是坏的」钉死，后面只照着 src 删。
    """
    return {i.get("src") or "" for i in info if i.get("nw") == 0}


def delete_targets(info, bad_srcs, keep):
    """在 info 里挑该删的下标：src 命中坏图名单，且总数留住 keep 张。"""
    hit = [i for i, it in enumerate(info) if (it.get("src") or "") in bad_srcs]
    spare = len(info) - keep
    return hit[:max(0, min(len(hit), spare))]


# ------------------------------------------------------------------ 浏览器
class Session(object):
    """一个专属标签页，串起「开抽屉 → 读卡片 → 开编辑器 → 换图」。"""

    def __init__(self, port=None):
        self.port = port or xp.PUB_PORT
        self.sub = None
        self.tid = None
        self.ok_index = None          # 上次真管用的那个 file input

    # --- 基础 ----------------------------------------------------------
    def open(self, fresh=True):
        cdp = wp.CDP(self.port)
        if fresh or self.tid is None:
            self.tid = cdp.new_target("about:blank")["id"]
        self.sub = wp.CDP(self.port)
        self.sub.connect_target(self.tid)
        self.sub.send("Page.navigate", {"url": xp.PUB_URL})
        time.sleep(8)
        self.sub.eval("1", refresh_context=True)
        return self.sub

    def js(self, expr):
        r = self.sub.eval(expr, refresh_context=True)
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except ValueError:
                pass
        return r

    def click_at(self, x, y, label=""):
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            try:
                self.sub.send("Input.dispatchMouseEvent",
                              {"type": typ, "x": x, "y": y, "button": "left",
                               "clickCount": 1})
            except Exception as e:
                print("    [warn] 点击失败（%s）：%s" % (label, e))
                return False
            time.sleep(0.12)
        return True

    def hover(self, x, y):
        try:
            self.sub.send("Input.dispatchMouseEvent",
                          {"type": "mouseMoved", "x": x, "y": y})
        except Exception:
            pass

    def close(self):
        if not self.tid:
            return
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            op.open("http://127.0.0.1:%d/json/close/%s" % (self.port, self.tid),
                    timeout=3).read()
        except Exception:
            pass

    # --- 草稿箱 --------------------------------------------------------
    def goto_publish(self):
        """确保在发布页 —— 页面是 SPA，导航完还得给它几秒挂载。"""
        for _ in range(3):
            url = self.js("location.href") or ""
            if "/publish" in url:
                break
            try:
                self.sub.send("Page.navigate", {"url": xp.PUB_URL})
            except Exception:
                return False
            time.sleep(8)
            self.js("1")
        # ?target=draft 有时会停在视频视图：刷一次回到干净状态
        if not self.js("""(function(){return [].slice.call(document.querySelectorAll('*'))
                      .filter(function(x){return /^草稿箱\\(\\d+\\)$/
                        .test((x.innerText||'').trim());}).length>0 ? 1:0;})()"""):
            try:
                self.sub.send("Page.navigate", {"url": xp.PUB_URL})
            except Exception:
                return False
            time.sleep(8)
            self.js("1")
        return True

    def open_drawer(self, tries=3):
        """点右上角「草稿箱(N)」开抽屉（真实鼠标点击才认）。"""
        self.goto_publish()
        for _ in range(tries):
            if self.js("""(function(){return (document.body.innerText||'')
                      .indexOf('图文笔记(')>=0 ? 1:0;})()"""):
                return True
            box = self.js("""(function(){
              var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
                return /^草稿箱\\(\\d+\\)$/.test((x.innerText||'').trim()) &&
                       x.getBoundingClientRect().width>0;});
              if(!es.length) return null;
              var e=es[es.length-1]; var r=e.getBoundingClientRect();
              return JSON.stringify({x:Math.round(r.x+r.width/2),
                                     y:Math.round(r.y+r.height/2)});})()""")
            if box:
                self.click_at(box["x"], box["y"], "草稿箱入口")
            time.sleep(4)
        return self.js("""(function(){return (document.body.innerText||'')
                      .indexOf('图文笔记(')>=0 ? 1:0;})()""") == 1

    def list_drafts(self, timeout=30, rounds=3):
        """抽屉里读图文草稿卡片。

        ⚠ 抽屉会抽风：明明 43 条也会读出 0 条（SPA 没渲染完 / tab 被切回去）。
          ⇒ 读到空就重点一次「图文笔记」tab，rounds 次都没货才算真没有，
            并且**明说出来**，别静默当空箱处理。
        """
        for rd in range(rounds):
            self.js("(function(){%s\n" % JS_CLICK_CHAIN + """
              var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
                return /图文笔记\\(\\d+\\)/.test((x.innerText||'').trim()) &&
                       (x.innerText||'').trim().length<12 &&
                       x.getBoundingClientRect().width>0; });
              if(es.length){jclick(es[es.length-1]); return 1;} return 0;})()""")
            for _ in range(int(timeout / 2.5) or 1):
                time.sleep(2.5)
                if self.js("""(function(){return [].slice.call(document.querySelectorAll('*'))
                          .filter(function(x){return (x.innerText||'').indexOf('保存于')>=0;})
                          .length;})()"""):
                    break
            cards = self.js(JS_CARDS) or []
            if cards:
                return cards
            print("  [warn] 第 %d 轮读到 0 条卡片，重试" % (rd + 1))
            time.sleep(3)
        print("  [err] 连读 %d 轮都是空的 —— 不是空箱，是没渲染出来" % rounds)
        return []

    def open_editor(self, title, timeout=45):
        """点卡片「编辑」进编辑器。判据是**标题输入框有值**（导航里有常驻的
        「发布笔记」，拿页面含「发布」判定 100% 假阳性）。"""
        self.js(JS_OPEN_EDIT % json.dumps((title or "")[:20]))
        q = ("(function(){var e=document.querySelector(%s);"
             "return e&&(e.value||'').trim().length>0 ? (e.value||'').trim() : '';})()"
             % json.dumps(SEL_TITLE))
        for _ in range(int(timeout / 1.5) or 1):
            time.sleep(1.5)
            v = self.js(q)
            if v:
                return True, v
        return False, ""

    # --- 图片 ----------------------------------------------------------
    def img_info(self):
        r = self.js(JS_IMG_INFO)
        return r if isinstance(r, list) else []

    def img_health(self):
        """返回 (总张数, 裂图张数)。"""
        info = self.img_info()
        return len(info), sum(1 for i in info if i.get("nw") == 0)

    def wait_settled(self, stable=3, gap=1.2, tries=14):
        """等图片**数量稳定**再体检。返回 (张数, 是否稳定)。

        草稿刚打开时图是**一张一张填进来**的（实测先看到 1 张，几秒后变 5 张），
        不等就数会数出「1/1 张裂图」这种鬼数字。
        """
        last, same = None, 0
        for _ in range(tries):
            n = len(self.img_info())
            same = same + 1 if n == last else 0
            if same >= stable:
                return n, True
            last = n
            time.sleep(gap)
        return (last or 0), False

    def wait_count(self, want, tries=20, gap=1.5):
        for _ in range(tries):
            if len(self.img_info()) == want:
                return True
            time.sleep(gap)
        return False

    def wait_new_loaded(self, n_new, tries=30, gap=2.0):
        """等**最后 n_new 张**（新传的）全部加载出（naturalWidth>0）。"""
        for _ in range(tries):
            info = self.img_info()
            tail = info[-n_new:] if n_new else []
            if len(tail) == n_new and all(i.get("nw", 0) > 0 for i in tail):
                return True
            time.sleep(gap)
        return False

    def wait_new_saved(self, n_new, tries=30, gap=2.0):
        """（已弃用）新图 src 从 blob: 换正式地址的等待。

        实测预览的 src **永远不会**换（网络探针：上传 200、CDN 已能取到图，
        预览仍停在 blob:）——改用 SETTLE_UPLOAD 固定等待。方法留着备用，
        别再当保存判据。
        """
        for _ in range(tries):
            info = self.img_info()
            tail = info[-n_new:] if n_new else []
            if len(tail) == n_new and all(
                    (i.get("src") or "").startswith("http")
                    and not (i.get("src") or "").startswith("blob:")
                    for i in tail):
                return True
            time.sleep(gap)
        return False

    def delete_index(self, idx, tries=3):
        """删第 idx 张图；张数真的降了才算数（见模块 docstring 两条规矩）。"""
        info = self.img_info()
        if idx >= len(info):
            return False
        before = len(info)
        for _ in range(tries):
            cur = self.img_info()
            if idx >= len(cur):
                return True                      # 它自己没了，算删掉
            it = cur[idx]
            self.hover(4, 4)                     # 甩开 hover 死锁
            time.sleep(0.25)
            self.hover(it["x"], it["y"])
            time.sleep(0.5)
            d = self.js("(%s)(%d)" % (JS_CLOSE_AT, idx))
            if not isinstance(d, dict) or not d.get("onBtn"):
                continue                         # 按钮没显形，按下去就是误点
            x, y = d["cx"], d["cy"]
            self.hover(x, y)
            time.sleep(0.25)
            if not self.click_at(x, y, "删图"):
                continue
            time.sleep(0.9)
            if len(self.img_info()) < before:
                return True
            self.js("(function(){var its=[].slice.call("
                    "document.querySelectorAll('.img-container'));"
                    "var cb=its[%d]?its[%d].querySelector('.close-btn'):null;"
                    "if(!cb) return 0; %s jclick(cb); return 1;})()"
                    % (idx, idx, JS_CLICK_CHAIN))
            time.sleep(0.9)
            if len(self.img_info()) < before:
                return True
        return False

    def delete_broken(self, bad_srcs, keep):
        """照着 src 名单逐张删，**永远留住 keep 张**。返回删掉几张。"""
        removed = 0
        for _ in range(MAX_IMAGES + 5):
            info = self.img_info()
            todo = delete_targets(info, bad_srcs, keep)
            if not todo:
                break
            if not self.delete_index(todo[0]):
                print("    [warn] 有一张删不动了，停手（剩 %d 张）" % len(info))
                break
            removed += 1
            time.sleep(0.5)
        return removed

    def _file_nodes(self):
        doc = self.sub.send("DOM.getDocument", {"depth": 0})
        root = (doc.get("result") or {}).get("root", {}).get("nodeId")
        res = self.sub.send("DOM.querySelectorAll",
                            {"nodeId": root, "selector": SEL_FILE_IMG})
        ids = (res.get("result") or {}).get("nodeIds") or []
        if not ids:
            q = self.sub.send("DOM.querySelector",
                              {"nodeId": root, "selector": SEL_FILE_IMG})
            nid = (q.get("result") or {}).get("nodeId")
            ids = [nid] if nid else []
        return ids

    def upload(self, paths, gap=1.0):
        """逐张传本地图：先拽起 publishers 真实file input 再塞文件。"""
        done, failed = [], []
        for p in paths:
            before = len(self.img_info())
            hit = False
            for idx, nid in enumerate(self._file_nodes()):
                try:
                    self.sub.send("DOM.setFileInputFiles",
                                  {"nodeId": nid, "files": [p]})
                except Exception as e:
                    print("    [warn] setFileInputFiles(%d)：%s" % (idx, e))
                    continue
                if self.wait_count(before + 1, tries=25, gap=1.2):
                    self.ok_index = idx
                    hit = True
                    break
            if hit:
                done.append(p)
            else:
                failed.append(p)
                print("    [warn] %s 没传上去" % os.path.basename(p))
            time.sleep(gap)
        return done, failed

    def replace_images(self, paths):
        """换图主流程：**先传后删**。返回统计字典。

        ① 体检时把裂图的 src 记下来（bad_srcs）—— 传完图后新图也会有短暂的
           naturalWidth===0，靠 nw 认裂图会把新图误删。
        ② 算清楚还**缺几张**：上一轮传过又没删干净的好图要认账，
           只补差额（试点跑一半重跑时，不认账就会多传一倍）。
        ③ 传本地图（追加在尾部）
        ④ 等新图加载出
        ⑤ 照名单删裂图，至少留 len(paths) 张
        """
        info0 = self.img_info()
        bad = broken_srcs(info0)
        before_total, before_broken = len(info0), len(bad)
        already_good = before_total - before_broken
        need = max(0, len(paths) - already_good)
        up, fail, loaded, saved = [], [], True, True
        pre_removed = 0
        if before_total + need > MAX_IMAGES:       # 别把 18 张上限顶爆
            pre_removed = self.delete_broken(bad, keep=MAX_IMAGES - need)
        if need:
            up, fail = self.upload(paths[:need])
            loaded = self.wait_new_loaded(need)
            time.sleep(SETTLE_UPLOAD)
        removed = self.delete_broken(bad, keep=len(paths))
        total, broken = self.img_health()
        return {"before_total": before_total, "before_broken": before_broken,
                "pre_removed": pre_removed, "uploaded": len(up),
                "failed": len(fail), "loaded": loaded,
                "removed": removed, "total": total, "broken": broken}

    def leave(self, save=True):
        """离开编辑页。save=True 时显式点「暂存离开」把改动存进草稿。

        两条实测规矩（2026-10-20 探针定的，违者改动全丢）：
        1. 导航离开触发的「自动暂存」会丢改动（上传中的图被整批丢、
           删掉的图原地复活）—— 必须点 <xhs-publish-btn> closed shadow DOM
           里的白色「暂存离开」按钮（class=white，跟红「发布」同宿主）。
        2. 点按钮前先等 SETTLE_EDIT：应用对删图/上传有异步收尾，
           收尾没做完就保存，快照的还是旧状态。
        save=False（干跑/跳过，没动过内容）直接导航走，省时间。
        """
        if save:
            time.sleep(SETTLE_EDIT)
            try:
                st = xp.publish_btn_state(self.sub)
                host = st.get("host") or {}
                if str(host.get("save-disabled", "")).lower() != "true":
                    nid, coord = xp.shadow_publish_btn(self.sub, cls="white")
                    if nid and coord:
                        xp._click_shadow_btn(self.sub, coord)
                        print("    已点「暂存离开」（shadow 按钮）")
                        time.sleep(10)
                        self.js("1")
                        return self.js(xp._JS_DRAFT_SIGNAL) or {}
                print("    [warn] 暂存按钮点不了，退回导航离开（改动可能丢）")
            except Exception as e:
                print("    [warn] 显式暂存失败（%s），退回导航离开" % e)
        try:
            self.sub.send("Page.navigate", {"url": xp.HOME_URL})
        except Exception as e:
            print("    [warn] 离开失败：%s" % e)
        time.sleep(6)
        self.js("1")
        return self.js(xp._JS_DRAFT_SIGNAL) or {}


# ------------------------------------------------------------------ 主流程
def load_plan(drafts, force=False):
    """草稿 ←→ 本地卡片图。"""
    title_map = {}
    for c in xp.load_cases():
        nj = os.path.join(OUT, c["id"], "note.json")
        if not os.path.isfile(nj):
            continue
        with open(nj, encoding="utf-8") as f:
            note = json.load(f)
        title_map[xp.norm_title(note.get("title", ""))] = (c["id"], note)
    rows = []
    for d in drafts:
        key = xp.norm_title(d.get("title") or "")
        hit = title_map.get(key)
        if hit is None:                       # 标题被改写过：退一步包含匹配
            for k, v in title_map.items():
                if k and len(k) >= 6 and (k in key or key in k):
                    hit = v
                    break
        cid, note = hit if hit else ("", {})
        local, miss = [], []
        if cid:
            local, miss = resolve_local(os.path.join(OUT, cid),
                                        note.get("images") or [])
        rows.append({"title": d.get("title", ""), "saved": d.get("saved", ""),
                     "cid": cid, "local": local, "miss": miss,
                     "total": 0, "broken": 0, "force": force})
    return rows


def main():
    ap = argparse.ArgumentParser(description="小红书草稿箱：裂图重传本地卡片图")
    ap.add_argument("--dry", action="store_true", help="只体检，不动手")
    ap.add_argument("--limit", type=int, default=0, help="最多修几条（0=全部）")
    ap.add_argument("--only", default="", help="只修标题含该关键词的草稿")
    ap.add_argument("--force", action="store_true", help="图片正常的也重传一遍")
    ap.add_argument("--verify", action="store_true",
                    help="修完重开草稿复检（验证真的落进了草稿，慢一倍）")
    ap.add_argument("--report", default="tmp/xhs_fix_report.json",
                    help="体检/修复结果写哪")
    args = ap.parse_args()

    if not xp._port_up(xp.PUB_PORT):
        raise SystemExit(
            "调试 Chrome 没起来（%d）。先跑：\n"
            "  python scripts/_launch_9223.py --hold 3600" % xp.PUB_PORT)

    root_dir = os.path.dirname(_HERE)
    s = Session()
    s.open(fresh=True)
    if not s.open_drawer():
        raise SystemExit("[err] 打不开草稿箱抽屉，去浏览器看一眼")
    drafts = s.list_drafts()
    print("草稿箱 %d 条图文草稿" % len(drafts))

    rows = load_plan(drafts, force=args.force)
    nomap = [r for r in rows if not r["cid"]]
    print("认出案例 %d 条，认不出 %d 条" % (len(rows) - len(nomap), len(nomap)))
    for r in nomap:
        print("  ? %s %s" % (r["saved"], r["title"][:30]))

    targets = [r for r in rows if r["cid"] and r["local"]]
    if args.only:
        targets = [r for r in targets if args.only in r["title"]]
    if args.limit:
        targets = targets[:args.limit]
    print("待处理 %d 条\n" % len(targets))

    report = []
    done_n = ok_n = 0
    try:
        for i, r in enumerate(targets, 1):
            print("[%d/%d] %s（%s）" % (i, len(targets), r["title"][:34],
                                       r["saved"]))
            # 每轮重读列表：草稿会被自动重存，**顺序会变**
            if not s.open_drawer():
                s.open(fresh=True)
                if not s.open_drawer():
                    print("  [err] 抽屉打不开，重开标签页也没用，停在这儿")
                    break
            cur = s.list_drafts()
            want = xp.norm_title(r["title"])
            now = next((d for d in cur
                        if xp.norm_title(d.get("title") or "") == want), None)
            if now is None:
                print("  [skip] 草稿箱里找不到这条了（已发布/被删？）")
                report.append(dict(r, result="missing"))
                continue
            ok, got = s.open_editor(now.get("title"))
            if not ok:
                print("  [skip] 编辑器没打开")
                report.append(dict(r, result="no-editor"))
                continue
            n, settled = s.wait_settled()
            if not settled:
                print("  [warn] 图片数量没稳住（数到 %d 张），体检可能偏乐观" % n)
            total, broken = s.img_health()
            r["total"], r["broken"] = total, broken
            plan = plan_repair([r])[0]
            print("  体检：%s" % plan["reason"])
            if args.dry or plan["action"] != "repair":
                report.append(dict(r, result="dry" if args.dry
                                   else plan["action"]))
                s.leave(save=False)
                continue
            out = s.replace_images(r["local"])
            print("  上传 %d 张（失败 %d），新图%s"
                  % (out["uploaded"], out["failed"],
                     "已加载" if out["loaded"] else "加载超时"))
            print("  删裂图 %d 张 → 剩 %d 张 / 裂 %d 张"
                  % (out["removed"], out["total"], out["broken"]))
            good = (out["broken"] == 0 and out["total"] == len(r["local"])
                    and out["failed"] == 0 and out["loaded"])
            sig = s.leave()
            print("  离开编辑器 → 暂存信号 %s" % (sig or "（没读到）"))
            if args.verify:
                # 真验收：重开这条草稿，看改动是不是真的进了草稿。
                vt = vb = -1
                if s.open_drawer():
                    cur2 = s.list_drafts()
                    now2 = next((d for d in cur2
                                 if xp.norm_title(d.get("title") or "") == want),
                                None)
                    if now2 and s.open_editor(now2.get("title"))[0]:
                        s.wait_settled()
                        vt, vb = s.img_health()
                        print("  重开复检：%d 张，裂 %d 张" % (vt, vb))
                        s.leave()
                r["reopen_total"], r["reopen_broken"] = vt, vb
                good = good and vb == 0
            done_n += 1
            ok_n += 1 if good else 0
            report.append(dict(r, result="ok" if good else "partial", **out,
                               signal=sig))
    finally:
        s.close()
        if args.report:
            path = os.path.abspath(os.path.join(root_dir, args.report))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print("\n报告：%s" % path)
    print("处理 %d 条，其中修正常 %d 条" % (done_n, ok_n))


if __name__ == "__main__":
    main()
