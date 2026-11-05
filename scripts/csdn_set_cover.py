#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 封面上传（唯一可行路径：编辑器「发布文章」抽屉里的上传 + 裁剪）。

两种用法：
  # 1) 给已发布/草稿文章补封面（会再提交一次，触发重新审核）
  python -X utf8 scripts/csdn_set_cover.py <articleId> <cover.png> [--publish]

  # 2) 只上传拿 URL，不碰任何文章 —— 发表时把 URL 塞给 saveArticle 的 cover_images
  python -X utf8 scripts/csdn_set_cover.py --upload <cover.png>

2026-11-04 实测链路（照这个抄，别自己猜选择器）：
  **`input.el-upload__input`**（Element UI，accept=".png,.jpg,.jpeg,.gif"）喂图
  → 弹 `.vue-image-crop-upload`（标题「图片编辑」）→ 点 `.vicp-operate` 里的「确认上传」
  → `.preview-box img.preview` 的 src 变成 `i-blog.csdnimg.cn/direct/<hash>.png`
  → 抽屉底部「发布文章」。

🚨 老选择器 `.cover-upload-box input[type=file]` **恒返回空**（那个 input 不在 box 里）。
   2026-11-04 被它耽误大半天，症状是「上传封面永远报抽屉没开」。现保留为三级回退，**永远排最后**。
⚠ 页面里有三个 `input[type=file]`：`.hidden-file`（导入 md）、`.cfw-file-input`（创作问题反馈）、
   `.el-upload__input`（封面）。喂错就白跑，别用 `input[type=file]` 这种泛选择器。
⚠ **>2MB 的图传不上去**（5.6MB/3845×2157 喂进去 40s 无回图）。`ensure_cover()` 会在开抽屉前
   自动把超限图缩到 700 宽；缩不了就抛错，**绝不空等 40 秒**。
⚠ `location.href` 丢了 `?articleId=` ⇒ 这篇处于审核未通过/回收站，编辑器开的是空白新稿，
  此刻点发布 = 新建一篇（2026-11-04 踩过），所以必须先把 href 校验一遍。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ.setdefault("NO_PROXY", "*")

import chrome_cdp_launch as L          # noqa: E402
import csdn_publish as C               # noqa: E402
import wechat_publish as wp            # noqa: E402

PORT = 9224

# 🚨 实测上限：>2MB 的图喂进抽屉，40 秒内 services 都不回 csdnimg URL（表现为 `NO_IMG`）。
#   0.1MB 同尺寸秒回。所以开抽屉之前必须先把图压下来，别等超时。
COVER_MAX_BYTES = 2 * 1024 * 1024
COVER_TARGET_WIDTH = 700      # 700 最稳（实测 960 也行，700 一次过）
COVER_ACCEPT = (".png", ".jpg", ".jpeg", ".gif")

# 缩图用本机 skill 的零依赖实现（没装 Pillow 也能跑）
RESIZE_PY_CANDIDATES = (
    os.path.expanduser("~/.workbuddy/skills/zero-dep-image-inspect/scripts/inspect.py"),
)


def image_size(path):
    """读 PNG/JPEG 宽高，不依赖 Pillow。认不出返回 (0, 0)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return (int.from_bytes(head[16:20], "big"),
                        int.from_bytes(head[20:24], "big"))
            if head[:2] == b"\xff\xd8":                     # JPEG：扫 SOF 段
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        break
                    marker = b[0]
                    seg = f.read(2)
                    if len(seg) < 2:
                        break
                    length = int.from_bytes(seg, "big") - 2
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                                  0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        d = f.read(5)
                        if len(d) < 5:
                            break
                        return (int.from_bytes(d[3:5], "big"),
                                int.from_bytes(d[1:3], "big"))
                    f.seek(length, 1)
    except OSError:
        pass
    return (0, 0)


def _resize_script():
    for p in RESIZE_PY_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""


def ensure_cover(path, log=print):
    """开抽屉前调用：超 2MB 就地缩图，返回可直接喂给上传控件的路径。

    ⚠ 这里**不做静默降级** —— 找不到缩图工具就抛错并给出可复制的命令，
      因为硬喂大图的结果是白等 40 秒再报一句「抽屉没开？」。
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise SystemExit("✗ 封面文件不存在：%s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in COVER_ACCEPT:
        raise SystemExit("✗ CSDN 封面只收 %s，拿到 %s" % ("/".join(COVER_ACCEPT), ext))

    n = os.path.getsize(path)
    w, h = image_size(path)
    if n <= COVER_MAX_BYTES:
        log("  封面 %.2fMB %dx%d，直接用" % (n / 1048576.0, w, h))
        return path

    out = os.path.join(os.path.dirname(path),
                       "csdn-%dw-%s" % (COVER_TARGET_WIDTH, os.path.basename(path)))
    script = _resize_script()
    if not script:
        raise SystemExit(
            "✗ 封面 %.2fMB 超过 %.0fMB 上限（CSDN 传不上去），且本机找不到缩图脚本。\n"
            "  先手动缩图再跑：\n"
            "    python \"<zero-dep-image-inspect>/scripts/inspect.py\" resize %s --width %d -o %s\n"
            % (n / 1048576.0, COVER_MAX_BYTES / 1048576.0, path, COVER_TARGET_WIDTH, out))

    log("  封面 %.2fMB %dx%d 超限 → 缩到 %d 宽" % (n / 1048576.0, w, h, COVER_TARGET_WIDTH))
    subprocess.run([sys.executable, "-X", "utf8", script, "resize", path,
                    "--width", str(COVER_TARGET_WIDTH), "-o", out],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise SystemExit("✗ 缩图失败（%s 没生成），换个尺寸重试" % out)
    n2 = os.path.getsize(out)
    w2, h2 = image_size(out)
    log("  → %s（%.2fMB %dx%d）" % (os.path.basename(out), n2 / 1048576.0, w2, h2))
    if n2 > COVER_MAX_BYTES:
        raise SystemExit("✗ 缩到 %d 宽仍有 %.2fMB，改更小宽度" % (COVER_TARGET_WIDTH, n2 / 1048576.0))
    return out

JS_OPEN_DRAWER = r"""(function () {
  var all = document.querySelectorAll('button');
  for (var i = 0; i < all.length; i++) {
    if (/^发布文章$/.test((all[i].innerText || '').trim())) { all[i].click(); return 'ok'; }
  }
  return 'NOT_FOUND';
})()"""


def _open_drawer_real_click(cdp, log=print, wait_total=24):
    """真实坐标点击「发布文章」开抽屉并轮询 file input 出现。

    🚨 2026-11-05 实测：JS_OPEN_DRAWER 的合成 `.click()` 是 untrusted 事件，
    Vue handler 时认时不认 ⇒ 抽屉「时开时不开」（表现为找不到
    `input.el-upload__input`，十次里成一次全靠运气）。改成 CDP 坐标级
    `Input.dispatchMouseEvent`（trusted 事件）+ 轮询，治本。
    ⚠ 另一前置：**空白编辑器点发布不开抽屉**（只弹提示 toast）——
    先填占位标题+正文再点（封面 URL 之后随 saveArticle 提交，占位内容不落库）。
    """
    import json as _json
    # 占位内容（空白编辑器点发布只弹「请先…」提示，不出抽屉）
    cdp.eval(r"""(function(){
        var ti = document.querySelector('input.article-bar__title');
        if (ti && !ti.value) { ti.value='占位标题'; ti.dispatchEvent(new Event('input',{bubbles:true})); }
        var ce = document.querySelector('[contenteditable=true]');
        if (ce && !ce.innerText.trim()) {
            ce.innerHTML = '<p>占位正文。</p>';
            ce.dispatchEvent(new InputEvent('input',{bubbles:true}));
        }
        return 1;
    })()""")
    time.sleep(2)
    import json as _json
    box = cdp.eval(r"""(function(){
        // 优先 btn-publish（实测唯一可靠锚点）；按文字兜底时必须过滤不可见按钮，
        // 否则会命中隐藏的同名按钮 ⇒ 坐标 (0,0) 点在空白处。
        var b = document.querySelector('button.btn-publish');
        if (!(b && b.getBoundingClientRect().width > 0)) {
            var all = document.querySelectorAll('button');
            b = null;
            for (var i=0;i<all.length;i++){
                if (/^发布文章$/.test((all[i].innerText||'').trim())
                    && all[i].getBoundingClientRect().width > 0){ b = all[i]; break; }
            }
        }
        if (!b) return '';
        var r = b.getBoundingClientRect();
        return JSON.stringify({x:r.left+r.width/2, y:r.top+r.height/2});
    })()""")
    if not box:
        return "NOT_FOUND"
    b = _json.loads(box)
    for typ in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": typ, "x": b["x"], "y": b["y"],
                  "button": "left", "clickCount": 1})
    # 轮询抽屉里的 file input（渲染有延迟）
    for _ in range(max(1, wait_total // 3)):
        time.sleep(3)
        hit = cdp.eval(
            "!!(document.querySelector('.el-upload__input')"
            " || document.querySelector('input.cfw-file-input')"
            " || document.querySelector('.container-coverimage-box input[type=file]')"
            " || document.querySelector('.cover-upload-box input[type=file]'))")
        if hit:
            return "ok"
    return "INPUT_TIMEOUT"

JS_CONFIRM_CROP = r"""(function () {
  var w = document.querySelector('.vue-image-crop-upload');
  if (!w) return 'NO_CROPPER';
  var op = w.querySelector('.vicp-operate');
  if (!op) return 'NO_OPERATE';
  var cands = [].slice.call(op.querySelectorAll('a,button,span,div'));
  for (var i = 0; i < cands.length; i++) {
    var t = (cands[i].innerText || '').trim();
    if (/保存|确定|确认|完成/.test(t) && t.length < 6) { cands[i].click(); return 'clicked ' + t; }
  }
  var last = cands[cands.length - 1];
  if (last) { last.click(); return 'clicked-last ' + (last.innerText || '').trim(); }
  op.click(); return 'clicked-operate';
})()"""

JS_PREVIEW = r"""(function () {
  var im = document.querySelector('.preview-box img.preview');
  var src = im ? (im.getAttribute('src') || '') : '';
  if (src.indexOf('csdnimg.cn') > -1) return src.slice(0, 200);
  // 大图（>3MB）上传慢，抽屉顶部缩略图可能先出现
  var all = document.querySelectorAll('.modal img, [class*=cover] img, [class*=upload] img');
  for (var i = 0; i < all.length; i++) {
    var s = all[i].getAttribute('src') || '';
    if (s.indexOf('csdnimg.cn') > -1) return s.slice(0, 200);
  }
  return src ? src.slice(0, 200) : 'NO_IMG';
})()"""

JS_DRAWER_PUBLISH = r"""(function () {
  var btns = document.querySelectorAll('button');
  for (var i = btns.length - 1; i >= 0; i--) {
    var t = (btns[i].innerText || '').trim();
    var r = btns[i].getBoundingClientRect();
    if (/^发布文章$/.test(t) && r.height > 0 && r.width > 0 && r.left > window.innerWidth * 0.4) {
      btns[i].click(); return 'clicked';
    }
  }
  return 'NO_DRAWER_PUBLISH_BTN';
})()"""


def open_cdp(port=PORT, headed=True, timeout=45):
    """起一个可见 Chrome 并连上 CDP（Chrome 必须是本进程子进程，否则被一起收走）。"""
    L.clear_proxy()
    L.remove_lock(C.LOGIN_PROFILE)
    chrome, _ = L.pick_chrome()
    args = L.build_args(chrome, C.LOGIN_PROFILE, port=port, headless=not headed)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if not L.wait_cdp(proc, timeout=timeout, port=port):
        proc.terminate()
        raise SystemExit("✗ Chrome CDP 没起来（端口 %d）" % port)
    time.sleep(2)
    return proc, wp.CDP(port)


def _feed_and_confirm(cdp, cover, log=print):
    """在当前已打开抽屉的编辑器里：喂图 → 裁剪确认 → 返回封面 URL。"""
    cdp.send("DOM.enable")
    doc = cdp.send("DOM.getDocument", {"depth": 0})
    root = (doc.get("result") or {}).get("root", {}).get("nodeId")
    # 🚨 2026-11-05 实测抽屉里真正的封面上传控件是 Element UI 的
    #   `input.el-upload__input`（accept=".png,.jpg,.jpeg,.gif"），它**不在**
    #   `.cover-upload-box` 里面 —— 老的 `.cover-upload-box input[type=file]`
    #   选择器恒返回空，所以「上传封面」一直报「抽屉没开？」。
    #   2026-11-05 下午再变：抽屉改版后是 cfw-* 组件，控件变成
    #   `input.cfw-file-input`（accept 含 .webp），实测喂图直出 csdnimg URL。
    node = None
    for sel in ("input.cfw-file-input",
                ".el-upload__input",
                ".container-coverimage-box input[type=file]",
                ".cover-upload-box input[type=file]"):
        q = cdp.send("DOM.querySelector", {"nodeId": root, "selector": sel})
        node = (q.get("result") or {}).get("nodeId")
        if node:
            log("  上传控件：%s" % sel)
            break
    if not node:
        return "", "找不到封面上传的 file input（抽屉没开？）"
    cdp.send("DOM.setFileInputFiles", {"nodeId": node, "files": [os.path.abspath(cover)]})
    time.sleep(4)
    log("  确认裁剪：%s" % cdp.eval(JS_CONFIRM_CROP))
    # 大图（5MB+）上传 + 服务端处理后回图要十几秒，轮询别死等
    url = ""
    for i in range(10):
        time.sleep(4)
        url = str(cdp.eval(JS_PREVIEW)).strip()
        if url.startswith("http") and "i-blog.csdnimg.cn" in url:
            return url.split("?")[0], ""
        log("    等待封面回图…(%ds)" % ((i + 1) * 4))
    return "", "封面预览没拿到 csdnimg 地址（拿到 %r）" % url[:60]


def upload_cover_url(cdp, cover, log=print):
    """在空白编辑器里把图传上去，只拿 URL，不发表。返回 (url, err)。"""
    cover = ensure_cover(cover, log)          # 先缩图，别开抽屉白等
    tid = cdp.new_target("https://editor.csdn.net/md/")["id"]
    cdp.connect_target(tid)
    time.sleep(16)
    st = _open_drawer_real_click(cdp, log)    # 2026-11-05：合成 click 不可靠，改真实坐标点击+轮询
    log("  点「发布文章」开抽屉：%s" % st)
    if st not in ("ok",):
        return "", "抽屉没打开（%s）" % st
    return _feed_and_confirm(cdp, cover, log)


def set_cover(cdp, article_id, cover, publish=False, log=print):
    """给指定文章设封面；`publish=True` 时顺带点抽屉里的发布（会触发重新审核）。"""
    cover = ensure_cover(cover, log)          # 先缩图，别开抽屉白等
    url = "https://editor.csdn.net/md/?articleId=%s" % article_id
    tid = cdp.new_target(url)["id"]
    cdp.connect_target(tid)
    time.sleep(18)
    here = str(cdp.eval("location.href"))
    if "articleId=" not in here:
        return "", "编辑器把 articleId 吞了（当前 %s）——这篇在审核中/回收站，先恢复成草稿" % here[:60]
    log("  标题：%s" % str(cdp.eval(
        "(document.querySelector('.article-bar-title input, input.title-input')||{}).value || 'NA'"))[:40])
    st = _open_drawer_real_click(cdp, log)    # 2026-11-05：同 upload_cover_url
    log("  点「发布文章」开抽屉：%s" % st)
    if st not in ("ok",):
        return "", "抽屉没打开（%s）" % st
    cover_url, err = _feed_and_confirm(cdp, cover, log)
    if err:
        return "", err
    if publish:
        log("  抽屉发布：%s" % cdp.eval(JS_DRAWER_PUBLISH))
        time.sleep(10)
        log("  提示：%s" % str(cdp.eval("document.body.innerText.replace(/\\n+/g,'|').slice(0,200)"))[:200])
    return cover_url, ""


def main():
    args = list(sys.argv[1:])
    do_pub = "--publish" in args
    args = [a for a in args if a != "--publish"]
    if "--upload" in args:
        args = [a for a in args if a != "--upload"]
        if not args:
            raise SystemExit("用法: --upload <cover.png>")
        proc, cdp = open_cdp()
        try:
            url, err = upload_cover_url(cdp, args[0])
            print(("✓ " + url) if url else ("✗ " + err))
            return 0 if url else 1
        finally:
            proc.terminate()
    if len(args) < 2:
        raise SystemExit("用法: csdn_set_cover.py <articleId> <cover.png> [--publish] | --upload <cover.png>")
    aid, cover = args[0], os.path.abspath(args[1])
    proc, cdp = open_cdp()
    try:
        url, err = set_cover(cdp, aid, cover, publish=do_pub)
        if err:
            print("✗ " + err)
            return 1
        print("✓ 封面已设：%s%s" % (url, "（已提交发布）" if do_pub else "（未发布）"))
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
