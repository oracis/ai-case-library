"""B站已发布图文/专栏的 **API 修改**（2026-10-27 定案，端点/形状来自真实请求抓包）。

| 操作 | 方法 | 路径 | 状态 |
|---|---|---|---|
| 读已发布文章（含 dyn_id / opus.content / edit_times） | GET | `/x/article/creative/article/view?aid=` | 可用 |
| **保存已发布文章的修改** | POST | `/x/dynamic/feed/edit/opus` | 形状已定，待真改验证 |

## edit/opus 请求形状（2026-10-27 用户 DevTools cURL 抓包定案）

query（全部参与 WBI 签名，b_wet 除外）：
    csrf=<bili_jct>&gaia_source=main_web
    &x-bili-locale-json={"c_locale":{"language":"zh","script":"Hans"},"always_translate":false}
    &x-bili-device-req-json={"platform":"web","device":"pc","spmid":"333.40225","mobi_app":"web_cn"}
    &w_rid=<md5>&wts=<unix>
    &b_wet=<gaia 风控 token，JS 生成 —— 先试省略，-352 再想办法>
    &dm_img_list=[]&dm_img_str=<b64 WebGL>&dm_cover_img_str=<b64 GPU>&dm_img_inter={"ds":[],"wh":[4034,3813,4],"of":[25,50,25]}

body（JSON）：
    {"raw_content": "",
     "opus_req": {"upload_id": "<mid>_<unix>_<seq>",
                  "opus": {"opus_source": 2, "title": ..., "content": {"paragraphs": [...]},
                           "article": {"category_id": 15, "list_id": 0, "originality": 0,
                                        "reproduced": 1, "cover": [{"url": ...}]},
                           "pub_info": {"editor_version": "eva3-4.0.0"}},
                  "scene": 12,
                  "meta": {"app_meta": {"from": "edit.creative.h5", "mobi_app": "web"}},
                  "option": {"aigc": 2, "close_comment": 0, "up_choose_comment": 0}},
     "dyn_id_str": "<文章的动态 id，来自 article/view>"}

## 要点

1. **编辑绑定的是 `dyn_id_str`（动态 id）**，不是 aid/cvid；每次修改可能生成新 dyn 版本
   → 改前必须现查 `article/view`，别缓存。
2. `opus.content.paragraphs` 与 `article/view` 返回的**同构**（para_type 1=正文 4=引用块，
   node 形状与 draft/add 相同，引用块多 `format.combine_hash`="blockquote_<ts>_<rand>"）。
3. 每篇有修改次数上限（`edit_times` 已用次数，编辑器有 edit_count_exceeded 检查）——
   **禁止无意义空改**。
4. 修改提交后进「审核中」，服务端几分钟后生效。
5. ⚠ 真实请求的 cookie（SESSDATA 等）不落盘；本模块所有请求都在浏览器 tab 上下文里
   fetch（credentials: include），cookie 由浏览器自带。
"""
import json
import re
import time

import bili_draft_api as ba

API = "https://api.bilibili.com"
ARTICLE_VIEW = API + "/x/article/creative/article/view"
EDIT_OPUS = API + "/x/dynamic/feed/edit/opus"

EDITOR_VERSION = "eva3-4.0.0"
# 设备指纹固定值（2026-10-27 真机抓包；dm_* 目前看是设备能力描述，非一次性 token）
DM_IMG_LIST = "[]"
DM_IMG_STR = "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ"  # WebGL 1.0 (OpenGL ES 2.0 Chromium)
DM_COVER_IMG_STR = ("QU5HTEUgKEludGVsLCBJbnRlbChSKSBIRCBHcmFwaGljcyA1MjAg"
                    "KDB4MDAwMDE5MTYpIERpcmVjdDNEMTEgdnNfNV8wIHBzXzVfMCwgRDNEMTEp"
                    "R29vZ2xlIEluYy4gKEludGVsKQ")
DM_IMG_INTER = '{"ds":[],"wh":[4034,3813,4],"of":[25,50,25]}'


def article_view(cdp, aid):
    """读已发布文章：返回 data（dyn_id_str / edit_times / editable / opus / content）。"""
    js = """(async () => {
      const r = await fetch(%s + '?aid=' + %s, {credentials: 'include'});
      return await r.text();
    })()""" % (json.dumps(ARTICLE_VIEW), json.dumps(str(aid)))
    raw = cdp.eval(js, refresh_context=True)
    j = json.loads(raw)
    if j.get("code") != 0:
        raise RuntimeError("article/view 失败：%s" % json.dumps(j, ensure_ascii=False)[:200])
    return j.get("data") or {}


def build_edit_arg(view, title=None, paragraphs=None, mid=None):
    """从 article/view 的返回造 edit/opus 的 body。

    title / paragraphs 不传 = 原样回传（无变更保存）；传了就替换。
    paragraphs 直接用 opus.content 的结构（同构，别自己发明）。
    """
    opus = view.get("opus") or {}
    art = opus.get("article") or {}
    content = json.loads(json.dumps(opus.get("content") or {"paragraphs": []}))
    if paragraphs is not None:
        content = {"paragraphs": paragraphs}
    new_title = (title or opus.get("title") or "").strip()
    if not new_title:
        raise RuntimeError("拿不到标题")
    sub_article = {
        "category_id": art.get("category_id", 15),
        "list_id": (art.get("list") or {}).get("id", art.get("list_id", 0)) or 0,
        "originality": art.get("originality", 0),
        "reproduced": art.get("reproduced", 0),
    }
    cov = art.get("cover") or []
    if cov:
        sub_article["cover"] = [{"url": (c.get("url") if isinstance(c, dict) else c)}
                                for c in cov][:1]
    mid = mid or 0
    return {
        "raw_content": "",
        "opus_req": {
            "upload_id": "%s_%d_1" % (mid, int(time.time())),
            "opus": {
                "opus_source": 2,
                "title": new_title,
                "content": content,
                "article": sub_article,
                "pub_info": {"editor_version": EDITOR_VERSION},
            },
            "scene": 12,
            "meta": {"app_meta": {"from": "edit.creative.h5", "mobi_app": "web"}},
            "option": {"aigc": 2, "close_comment": 0, "up_choose_comment": 0},
        },
        "dyn_id_str": str(view.get("dyn_id_str") or ""),
    }


def edit_opus(cdp, arg):
    """POST edit/opus（WBI 签名 query；在 member 页上下文 fetch，cookie 浏览器自带）。"""
    csrf = cdp.eval(ba._csrf_js(), refresh_context=True)
    if not csrf:
        raise RuntimeError("拿不到 bili_jct，先在浏览器登录 B站")
    qs = ba.wbi_sign({
        "csrf": csrf,
        "gaia_source": "main_web",
        "x-bili-locale-json": json.dumps(
            {"c_locale": {"language": "zh", "script": "Hans"},
             "always_translate": False}, ensure_ascii=False, separators=(",", ":")),
        "x-bili-device-req-json": json.dumps(
            {"platform": "web", "device": "pc", "spmid": "333.40225",
             "mobi_app": "web_cn"}, ensure_ascii=False, separators=(",", ":")),
        "dm_img_list": DM_IMG_LIST,
        "dm_img_str": DM_IMG_STR,
        "dm_cover_img_str": DM_COVER_IMG_STR,
        "dm_img_inter": DM_IMG_INTER,
    }, ba._wbi_key(cdp))
    url = EDIT_OPUS + "?" + qs  # b_wet 先省略：待一次真实修改验证服务端是否强校验
    js = """(async () => {
      const r = await fetch(%s, {method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(%s)});
      return await r.text();
    })()""" % (json.dumps(url), json.dumps(arg, ensure_ascii=False))
    raw = cdp.eval(js, refresh_context=True)
    try:
        return json.loads(raw)
    except Exception:                                    # noqa: BLE001
        raise RuntimeError("edit/opus 返回非 JSON：%r" % (raw,))


def _para_from_plain(text):
    """普通文本段（para_type=1），node 形状同 draft/add。"""
    return {"para_type": 1,
            "format": {"indent": {"first_line_indent": 0, "indent": 0}},
            "text": {"nodes": [{"node_type": 1, "word": {
                "words": text, "font_size": 17, "color": "",
                "dark_color": "", "style": {}, "font_level": "regular"}}]}}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="B站已发布图文 API 修改")
    ap.add_argument("--aid", required=True)
    ap.add_argument("--title", help="新标题（不传=原样）")
    ap.add_argument("--dry", action="store_true", help="只构建 arg 不提交")
    a = ap.parse_args()
    import wechat_publish as wp
    cdp = wp.CDP(9223)
    ba._member_tab(cdp)
    view = article_view(cdp, a.aid)
    print("aid=%s | edit_times=%s | editable=%s"
          % (a.aid, view.get("edit_times"), view.get("editable")))
    print("dyn_id_str:", view.get("dyn_id_str"))
    print("现标题:", (view.get("opus") or {}).get("title"))
    try:
        mid = ba.my_mid(cdp)
    except Exception:                                    # noqa: BLE001
        mid = 0
    arg = build_edit_arg(view, title=a.title, mid=mid)
    body = json.dumps(arg, ensure_ascii=False)
    print("arg %d chars | title=%r | paragraphs=%d" % (
        len(body), arg["opus_req"]["opus"]["title"],
        len(arg["opus_req"]["opus"]["content"]["paragraphs"])))
    if a.dry:
        print("[dry] 不提交")
        return
    r = edit_opus(cdp, arg)
    print("resp:", json.dumps(r, ensure_ascii=False)[:300])
    if r.get("code") == 0:
        print("✅ 已提交，进审核，几分钟后生效")
    else:
        print("❌ code=%s msg=%s" % (r.get("code"), r.get("message")))


if __name__ == "__main__":
    main()
