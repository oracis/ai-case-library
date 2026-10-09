"""B站草稿箱的**接口层**封装（2026-09-30 实测）。

结论先说：**B站有草稿 API，而且能完全替代 UI 操作。** 头条没有。

四个接口（全部实测确认，不是照抄路径）：

| 操作 | 方法 | 路径 | 状态 |
|---|---|---|---|
| 读草稿详情 | GET  | `/x/dynamic/feed/article/draft/view?article_id=<id>` | 可用 |
| 读草稿列表 | GET  | `/x/dynamic/feed/article/draft/list?pn=1&ps=200&keyword=` | 可用 |
| **新建/更新草稿** | POST | `/x/dynamic/feed/article/draft/add` | **可用** |
| 删草稿 | POST | `/x/dynamic/feed/article/draft/delete` | 可用 |

## 关键：add 一个接口兼做新建与更新

`read-editor` 的 bundle（`sunflower/read-editor/assets/index-*.js`）里原文：

```js
const getDraftDetail = async ue => {
  const re = await httpSvc$1.request({method:"GET",
    url:"/x/dynamic/feed/article/draft/view", params:{article_id:ue}});
  ...
};
const saveDraftDetail = async ue => {
  const re = await httpSvc$1.request({method:"POST",
    url:"/x/dynamic/feed/article/draft/add", data:{arg:ue}});
  ...
};
```

三条要点，每条都踩过：

1. **没有 `update` / `modify` 路径**。实测 `draft/update`、`draft/modify`、
   `draft/publish`、`draft/editor` 全是**真 404**（连 `{code,msg}` 都没有，
   返回的是 HTML 错误页）。`draft/add` 是唯一的写入面。
2. **参数包在 `arg` 里**：body 是 `{"arg": {..}}` 的 **JSON**。
   平铺字段会得到 `-400 请求错误`（实测）。
3. **请求形状是「JSON body + URL 侧 csrf + WBI 签名」**（2026-10-01
   hook 真实 XHR 定案，**之前这三条全写错了**）：
   ```
   POST //api.bilibili.com/x/dynamic/feed/article/draft/add
         ?csrf=<bili_jct>&x-bili-locale-json=...&x-bili-device-req-json=...
         &w_rid=<md5>&wts=<unix>
   Content-Type: application/json
   {"arg": {...}}
   ```
   - `csrf` 在 **query**，不在 body（放 body 会 `-400`，不是 `-111`）。
   - `w_rid` = `md5(query + mixin_key)`，query 要含 `wts` 且**键排序**。
     mixin_key 来自免签的 `/x/web-interface/nav` 的 `wbi_img` 两张图
     文件名按 `MIXIN_TAB` 换位换序拼接截 32。
   - 段落 node是 `{node_type:1, word:{words,font_size:17,color:"",dark_color:"",
     style:{},font_level:"regular"}}` —— **不是** `{type:"text",text:...}`。
     缺 `font_size` 等`word` 子字段也是 -400。
   - bundle 里 `stringifyQueryString` 那段是给**表单类**接口用的，
     `draft/add` 走JSON 分支走不到。读压缩代码猜不出来，只有 hook 能。

## 抓请求形状的**唯一可靠办法是hook，不是读压缩代码**（2026-10-01 定案）

在这上面浪费了一整天：读5.1MB 的 `index-*.js` 把 `arg` 结构猜对了
（`type/template_id/category_id/opus` 那些），照样连`-400`。原因有三，
**三个都不是arg 的问题**：

| 项 | 猜错的（-400） | 真实 |
|---|---|---|
| Content-Type | `x-www-form-urlencoded` | **`application/json`** |
| csrf | form body | **URL query** |
| body | `csrf=..&arg=..` | **`{"arg":{...}}`** |
| 段落 node | `{type:"text",text:..}` | **`{node_type:1,word:{words,...}}`** |
| 签名 | 无 | **URL 带 `w_rid`(WBI md5) + `wts`** |

抓法（`scripts/probe_bili_save_capture.py`，可重复执行）：

```bash
python scripts/probe_bili_save_capture.py# 在空白编辑器填一段→hook→点保存→落盘→自动删新建的草稿
```

**⚠ axios 在浏览器里走 `XMLHttpRequest`，不hook `window.fetch` 什么也抓不到**
（这个坑单独让第一次尝试白跑）。hook `XMLHttpRequest.prototype.open/send`
把 `body` 存下来即可。

结论记在这里，以后**改协议相关代码前先跑一次抓包**，别再静态读 bundle。

## 为什么接口在 api.bilibili.com 而不是 member

从 `member.bilibili.com` 页面上下文里 fetch **相对路径**会打到 member
主机上，member 上没有这套路由 → **全 404**，看着像「接口不存在」，其实
是打错主机。必须用绝对 URL `https://api.bilibili.com/...`，cookie 靠
`credentials:'include'` 带过去（同域 cookie 策略允许）。

## 抓接口路径的正确姿势

B站是 SPA + 路由懒加载：
- 静态 `<script src>` 只有 6 个，且 HTML 仅 3KB，抓不到业务代码；
- `performance.getEntriesByType('resource')` 在**没进到那个路由**时是空的；
- 必须**真导航进草稿箱/编辑器页**，等 chunk 加载完，再从 performance 里
  找带 `opus`/`draft`/`article` 的 `.js`，逐个 curl 下来正则搜路径。

编辑器页有个**同域 iframe**（`member.bilibili.com/york/read-editor?aid=<id>`），
保存逻辑在**那个 iframe** 的 `read-editor/assets/index-*.js` 里（5.4MB），
外层 page 的 bundle 里没有。

配套脚本：
    python scripts/probe_bili_draft_api.py     # 探测接口可用性
    python scripts/grep_bili_bundle.py         # 抓 bundle 里的路径
"""
import hashlib
import json
import os
import re as _re
import sys
import time
import urllib.parse

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402

API = "https://api.bilibili.com"
DRAFT_VIEW = API + "/x/dynamic/feed/article/draft/view"
DRAFT_LIST = API + "/x/dynamic/feed/article/draft/list"
DRAFT_ADD = API + "/x/dynamic/feed/article/draft/add"
DRAFT_DELETE = API + "/x/dynamic/feed/article/draft/delete"

DRAFT_PAGE = "https://member.bilibili.com/opus/management/drafts"


def _bili_tab(cdp, new_if_none=True, wait=8):
    """连一个已登录的 B站 page target；没有就新开草稿箱页。"""
    for t in cdp.list_targets():
        if "bilibili.com" in (t.get("url") or "") and t.get("type") == "page":
            cdp.connect_target(t["id"])
            return t["id"]
    if not new_if_none:
        return None
    tid = cdp.new_target(DRAFT_PAGE)["id"]
    time.sleep(wait)
    cdp.connect_target(tid)
    return tid


def _csrf_js():
    """取 bili_jct 的 JS 片段。必须在页面上下文里跑，node 里拿不到。"""
    return ("(function(){var m=document.cookie.match("
            "/(?:^|;\\s*)bili_jct=([^;]+)/);return m?m[1]:'';})()")


# ==========================================================================
# WBI 签名（draft/add 必带 w_rid/wts，缺了必-400）
# ==========================================================================
#
# 2026-10-01 hook 真实请求实测：
#   ...&w_rid=3a92ead0ab5c66e633e4978079c1f4dc&wts=1790794045
#
# 算法（社区通行的 B站 web WBI）：
#   mixin_key = shuffle(img_filename + sub_filename)[:32]
#   w_rid = md5(urlencode(sorted(params + wts)) + mixin_key)
#
# mixin_key 来自 **免签**的 /x/web-interface/nav —— nav 是老接口，
# B站至今没给它加签，所以能直接拿。img/sub 每天轮换，**别硬编码**。

_MIXIN_TAB = (0, 46, 46, 43, 70, 19, 68, 39, 2, 5, 100, 62, 6, 26, 18, 22,
              51, 45, 33, 54, 42, 41, 21, 8, 49, 45, 3, 30, 29, 39, 18, 38,
              56, 20, 9, 57, 36, 53, 28, 42, 16, 33, 9, 34, 5, 19, 52, 39)

_wbi_cache = {"key": None, "at": 0}


def mixin_key(img_url, sub_url):
    raw = ""
    for u in (img_url, sub_url):
        name = u.rsplit("/", 1)[-1].split(".")[0]
        raw += "".join(name[i] for i in _MIXIN_TAB if i < len(name))
    return raw[:32]


def wbi_sign(params, key):
    """给参数加 wts/w_rid，返回 query 串。key = mixin_key() 的结果。

    WBI 规范细节（少一条就签名不过）：
      · 所有键按 ASCII 排序
      · 值里 `!'()*` 要先剔掉再编码
      · wts 是 unix 秒
    """
    p = dict(params)
    p["wts"] = int(time.time())
    items = []
    for k in sorted(p.keys()):
        v = "".join(ch for ch in str(p[k]) if ch not in "!'()*")
        items.append((k, urllib.parse.quote(str(v), safe="")))
    q = urllib.parse.urlencode(items)
    p["w_rid"] = hashlib.md5((q + key).encode()).hexdigest()
    return urllib.parse.urlencode(sorted(p.items(), key=lambda kv: kv[0]))


def _wbi_key(cdp, ttl=3600):
    """取mixin key，进程内缓存 ttl 秒（img/sub 每天才换）。"""
    now = time.time()
    if _wbi_cache["key"] and now - _wbi_cache["at"] < ttl:
        return _wbi_cache["key"]
    js = """(async () => {
      const r = await fetch('https://api.bilibili.com/x/web-interface/nav',
        {credentials: 'include'});
      return await r.text();
    })()"""
    raw = cdp.eval(js, refresh_context=True)
    j = json.loads(raw)
    if j.get("code") != 0:
        raise RuntimeError("nav 取 wbi key 失败：%s" % json.dumps(j, ensure_ascii=False)[:200])
    img = (j.get("data") or {}).get("wbi_img") or {}
    key = mixin_key(img.get("img_url", ""), img.get("sub_url", ""))
    if not key:
        raise RuntimeError("nav 没返回 wbi_img，签名做不了")
    _wbi_cache["key"] = key
    _wbi_cache["at"] = now
    return key


# ==========================================================================
# arg 的真实结构（2026-10-01从 read-editor bundle 里扒出来，**别再猜**）
# ==========================================================================
#
# 原文（压缩后）：
#
#   It = async () => {
#     const yn = {
#       type: 4, template_id: 1, category_id: 15,
#       article_id: re.value, title: Re.value,
#       private_pub: Ue.value,
#       reprint: toBoolNumber(qe.value), original: toBoolNumber(Be.value),
#       list_id: Rt.value ?? 0,
#       comment_selected: toBoolNumber(Ge.value),
#       up_closed_reply: toBoolNumber(!Ke.value),
#       timer_pub_time: ...,
#       only_fans_level: ..., only_fans_dnd: ...,
#       image_urls: Qe.value ? [Qe.value] : undefined,
#       summary: Oe.value.slice(0, 250),
#       opus: {
#         opus_source: 2,
#         title: Re.value,
#         content: Pe.value,          // = convertSchemaToOpus(编辑器 JSON)
#         pub_info: {editor_version: EDITOR_VERSION},   // "eva3-4.0.0"
#         attachments: {is_aigc: 0, ...}
#       }
#     };
#     return saveDraftDetail(yn)
#   }
#
# ⚠⚠ **踩过的坑：把 draft/view 的返回整个当 arg 回传，一律 -400**（实测，
#    连只带 article_id 都拒）。原因有三，都已修正到 build_arg 里：
#
#   1. 字段名不一样：view 给 `category:{id,name,...}`，arg 要**扁平的
#      `category_id:15`**；view 给 `list:null`，arg 要 `list_id:0`。
#   2. view **没有** `type`/`template_id`/`opus` 这几个必填项，得自己补。
#   3. `opus.content` 是**结构化段落数组**（`{paragraphs:[...]}`），
#      不是 view 里那个纯文本 `content` 字符串。
#
# para_type（从 convertOpusToSchema 的 switch 反推）：
#   1 = 普通段落 / 9 = 标题    2 = 图片    4 = 引用块quote
#   5 = 有序列表              6 = 无序列表
TEXT_PARA, PIC_PARA, QUOTE_PARA, OL_PARA, UL_PARA, H_PARA = 1, 2, 4, 5, 6, 9
EDITOR_VERSION = "eva3-4.0.0"
CATEGORY_ID = 15          # 科技区（view 里category.id 也是 15）


def _word(text, font_level="regular"):
    """一个文本节点。⚠ 结构**只能照抄真实抓包**，见模块 docstring。

    `word` 里那6 个键（words/font_size/color/dark_color/style/font_level）
    一个都不能少 —— 少 `font_size` 也是 -400（实测）。
    font_size 固定 17 = 编辑器默认正文；标题更大，但服务端不校验这个值。
    """
    return {"node_type": 1,
            "word": {"words": text, "font_size": 17, "color": "",
                     "dark_color": "", "style": {}, "font_level": font_level}}


def _text_para(text):
    """普通段落。`format` 里也要有 indent（B站会读first_line_indent）。"""
    return {"para_type": TEXT_PARA,
            "format": {"indent": {"first_line_indent": 0, "indent": 0}},
            "text": {"nodes": [_word(text)]}}


def _para_from_text(text):
    """一行文本 → 一个段落。空行返回 None（不要交空段落进去）。"""
    t = (text or "").strip()
    return _text_para(t) if t else None


def _heading_para(text, level=1):
    """标题段。B站标题是 para_type=9 + `format.indent` + font_level。"""
    return {"para_type": H_PARA,
            "format": {"indent": {"first_line_indent": 0, "indent": 0}},
            "text": {"nodes": [_word(text, font_level="h%d" % max(1, min(level, 6)))]}}


# **整块匹配**（开标签 + 内容 + 同名列标签）。理由见build_arg 里的注释。
_BLOCK_RE = _re.compile(
    r"<(h[1-6]|p|blockquote|li|div|section)\b[^>]*>(.*?)</\1\s*>",
    _re.S | _re.I)


def build_arg(title, body_html, article_id=None, image_urls=None,
              summary=None, category_id=CATEGORY_ID,
              private_pub=2, original=0, reprint=1):
    """把编辑器 HTML 造出 draft/add 需要的 arg。

    `body_html`：走 `clean_body` 之后的正文 HTML。
    `image_urls`：封面图 URL 列表（不是文件路径！从 draft/view 抄）。

    ⚠ **只支持段落级映射**（h1-h6 → 标题段，blockquote → 引用段，
    其余 → 段落）。**行内样式（粗体/颜色/居中）不会保留** —— B站富文本
    格式在 opus.content 节点的 word.style 里，这里不构造。真要保留样式
    走 UI 路线（publish_one）。

    `private_pub` 默认 2：抓包实测编辑器自己存草稿时发的是 2（= 自己可见），
    不是 0。
    """
    # --- 1) 拆 HTML 成 (kind, text) 序列 ---
    #
    # ⚠ **别用「交替分支 + 兜底分支」一个大正则**（2026-10-01 踩过）：
    #   正则从左往右找**最左匹配**，兜底分支`(.*?)</(?:p|div|li|h[1-6])>`
    #   会比 `<p[^>]*>(.*?)</p>` 分支更早命中（它能跨过 `<p>` 开标签），
    #   结果后面真正的 <p>/<blockquote> 全被它吃掉 —— 表现为
    #   「标题变成普通段落、引用段消失」。
    #   正确做法：**整块匹配**（开标签 + 内容 + 同名列标签），逐块扫描。
    paras = []
    for m in _BLOCK_RE.finditer(body_html or ""):
        tag = m.group(1).lower()
        txt = _strip_tags(m.group(2))
        if not txt:
            continue
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            paras.append(_heading_para(txt, int(tag[1])))
        elif tag == "blockquote":
            # 引用块内换行（<br>）拆成多个引用段，B站 UI 也是一段一块
            for line in txt.split("\n"):
                p = _para_from_text(line)
                if p:
                    p["para_type"] = QUOTE_PARA
                    paras.append(p)
        elif tag == "hr":
            paras.append({"para_type": TEXT_PARA,
                          "format": {"indent": {"first_line_indent": 0,
                                                "indent": 0}},
                          "text": {"nodes": [_word("——")]}})
        else:                            # p / div / li / 落单文本
            for line in txt.split("\n"):
                p = _para_from_text(line)
                if p:
                    paras.append(p)
    if not paras:
        # 没有块级标签（纯裸文本）→ 按空行分段，别整篇塞成一段
        for chunk in _re.split(r"\n\s*\n", _strip_tags(body_html or "")):
            p = _para_from_text(chunk)
            if p:
                paras.append(p)
    if not paras:
        paras = [_text_para("（正文为空）")]

    # --- 2) 纯文本 = summary 的来源（编辑器里 summary 就是正文纯文本） ---
    plain = _strip_tags(body_html or "").strip()
    if summary is None:
        summary = plain[:250]

    arg = {
        "type": 4,
        "template_id": 1,
        "category_id": category_id,
        "title": title,
        "private_pub": private_pub,
        "reprint": 1 if reprint else 0,
        "original": 1 if original else 0,
        "list_id": 0,
        "comment_selected": 0,
        "up_closed_reply": 0,
        "timer_pub_time": 0,
        "only_fans_level": 0,
        "only_fans_dnd": 0,
        "summary": summary[:250],
        "opus": {
            "opus_source": 2,
            "title": title,
            "content": {"paragraphs": paras},
            "pub_info": {"editor_version": EDITOR_VERSION},
            "attachments": {"is_aigc": 0},
        },
    }
    if article_id:
        arg["article_id"] = article_id
    if image_urls:
        arg["image_urls"] = list(image_urls)[:1]     # B站只认首图作封面
    return arg


def _strip_tags(s):
    """剥标签取纯文本。**块级标签的闭合处必须留一个换行**。

    ⚠⚠ 2026-10-09 修（这是「B站表格样式不好看」的直接根因）：
      原来只有 `<br>` 换成 `\\n`，其余标签一律删掉不占位。于是
      blockquote 里的多个 `<p>` 被剥成
          `付费意愿 ｜ 3/5支付可达 ｜ 3/5合规空间 ｜ 2/5…`
      —— `build_arg` 的blockquote 分支是靠 `txt.split("\\n")` 拆行的，
      没有 `\\n` 就一行到底。而接口路线（`save_via_api`，也是
      `publish_one` 的**默认**）是B站当前的主路径。

      ⇒ 把块级标签的**闭标签**替换成换行。`</p>` 前后本来就不该有正文，
      插入换行不改变语义，却让 `split("\\n")` 重新生效。
      只处理闭标签（`<br>` 原本就有），开标签删掉即可。
    """
    s = _re.sub(r"<br\s*/?>", "\n", s or "")
    # 块级闭标签 → 换行占位。必须排在通用剥标签**之前**。
    s = _re.sub(r"</(?:p|div|li|h[1-6]|blockquote|section|tr|td)\s*>",
                "\n", s, flags=_re.I)
    s = _re.sub(r"<[^>]+>", "", s)
    return (s.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'")).strip()


def list_drafts(cdp, pn=1, ps=200, keyword=""):
    """返回草稿列表 [{article_id,title,...}, ...]。"""
    js = """(async () => {
      const r = await fetch(%s + '?pn=' + %d + '&ps=' + %d + '&keyword=' +
        encodeURIComponent(%s), {credentials: 'include'});
      const j = await r.json();
      if (j.code !== 0) return {err: j.code, msg: j.message};
      return {drafts: (j.data && j.data.drafts) || []};
    })()""" % (json.dumps(DRAFT_LIST), pn, ps, json.dumps(keyword))
    r = cdp.eval(js, refresh_context=True)
    if not isinstance(r, dict):
        raise RuntimeError("draft/list 返回异常：%r" % (r,))
    if r.get("err") is not None:
        raise RuntimeError("draft/list 失败：%s %s" % (r.get("err"), r.get("msg")))
    return r.get("drafts") or []


def view_draft(cdp, article_id):
    """读单条草稿详情（含 content / image_urls 等完整字段）。

    这是**填 `arg` 的模板来源** —— 读一条真实草稿，把要改的字段改了再
    整体回传给 add，就完成了一次「更新草稿」。
    """
    js = """(async () => {
      const r = await fetch(%s + '?article_id=' + %s,
        {credentials: 'include'});
      return await r.text();
    })()""" % (json.dumps(DRAFT_VIEW), json.dumps(str(article_id)))
    raw = cdp.eval(js, refresh_context=True)
    try:
        j = json.loads(raw)
    except Exception:                                    # noqa: BLE001
        raise RuntimeError("draft/view 返回非 JSON：%r" % (raw,))
    if j.get("code") != 0:
        raise RuntimeError("draft/view 失败：%s" % json.dumps(j, ensure_ascii=False)[:200])
    return (j.get("data") or {}).get("draft") or {}


def save_draft(cdp, arg):
    """新建或更新草稿。`arg` 用 `build_arg()` 造，别手拼。

    返回 {"code":.., "msg":.., "data":{...}}；code==0 才算成功。
    带 article_id 即更新，不带即新建 —— **没有单独的 update 接口**。

    ⚠ 三个曾经踩错的点（2026-10-01 hook 实测纠正，**别改回去**）：
      · Content-Type 是`application/json`，**不是** form-urlencoded
      · `csrf` 在 **URL query**，不在 body
      · URL 必须带 `w_rid`+`wts`（WBI 签名），否则 -400
    """
    csrf = cdp.eval(_csrf_js(), refresh_context=True)
    if not csrf:
        raise RuntimeError("拿不到 bili_jct（cookie 里没有），先在浏览器登录 B站")
    qs = wbi_sign({
        "csrf": csrf,
        "x-bili-locale-json": json.dumps(
            {"c_locale": {"language": "zh", "script": "Hans"},
             "always_translate": False}, ensure_ascii=False, separators=(",", ":")),
        "x-bili-device-req-json": json.dumps(
            {"platform": "web", "device": "pc", "spmid": "333.40225",
             "mobi_app": "web_cn"}, ensure_ascii=False, separators=(",", ":")),
    }, _wbi_key(cdp))
    url = DRAFT_ADD + "?" + qs
    js = """(async () => {
      const r = await fetch(%s, {method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({arg: %s})});
      return await r.text();
    })()""" % (json.dumps(url), json.dumps(arg, ensure_ascii=False))
    raw = cdp.eval(js, refresh_context=True)
    try:
        return json.loads(raw)
    except Exception:                                    # noqa: BLE001
        raise RuntimeError("draft/add 返回非 JSON：%r" % (raw,))


def delete_draft(cdp, article_id):
    """删草稿。实测返回 {"code":0,"message":"OK"}。"""
    js = """(async () => {
      const m = document.cookie.match(/(?:^|;\\s*)bili_jct=([^;]+)/);
      const csrf = m ? m[1] : '';
      const p = new URLSearchParams();
      p.set('article_id', %s);
      p.set('csrf', csrf);
      const r = await fetch(%s, {method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: p.toString()});
      return await r.text();
    })()""" % (json.dumps(str(article_id)), json.dumps(DRAFT_DELETE))
    raw = cdp.eval(js, refresh_context=True)
    try:
        return json.loads(raw)
    except Exception:                                    # noqa: BLE001
        raise RuntimeError("draft/delete 返回非 JSON：%r" % (raw,))


def main():
    # ⚠⚠ 2026-10-09 修：原来硬编码 9222（**公众号**那个 profile），
    #   而 B站登录态在 `chrome-debug-profile` = **9223**
    #   ⇒ 症状是「连不上 9222」，很容易误判成掉登录。
    #   跟B站自己的模块常量走，别再写死端口。
    import bilibili_publish as bp
    cdp = wp.CDP(bp.PUB_PORT)
    _bili_tab(cdp)
    drafts = list_drafts(cdp)
    print("草稿箱 %d 条" % len(drafts))
    for d in drafts[:5]:
        print("  article_id=%-10s %s" % (d.get("article_id"),
                                         (d.get("title") or "")[:40]))
    if not drafts:
        return 1
    aid = drafts[0]["article_id"]
    detail = view_draft(cdp, aid)
    keys = sorted(detail.keys())
    print("\ndraft/view 返回字段（%d 个）：%s" % (len(keys), keys))
    print("→ 这些字段就是 draft/add 的 arg 模板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
