# -*- coding: utf-8 -*-
"""公众号服务端 API 链路自检（换主体/认证之后跑这个）。

背景（2026-10-18 复核）：官方文档里 `draft/add` 等草稿箱接口对「公众号」标 ✔，
但**未认证个人订阅号实测一律 48001**；`freepublish` 更是「仅认证（企业主体）」。
所以本脚本的意义是：**等主体升级/认证后，用一条命令验证 API 到底通没通**，
而不是继续靠猜。

链路（全部官方接口，零第三方依赖）：
    1. get_access_token
    2. material/add_material  上传封面 → thumb_media_id
    3. draft/add              建一条探针草稿 → media_id
    4. draft/batchget         回读确认真的落库（不信 errcode=0）
    5. draft/delete           删掉探针草稿（--keep 可保留）

用法：
    set WX_APPID=wx...        （或 --appid）
    set WX_APPSECRET=...      （或 --secret）
    python -X utf8 scripts/probe_wechat_openapi.py
    python -X utf8 scripts/probe_wechat_openapi.py --keep

退出码：0 全链路通；1 某步失败（会打印错误码含义与下一步）；2 用法错误。

⚠ 必须先做两件事，否则一定失败：
  ① 微信开发者平台 → 该公众号 → 基本配置：拿到 AppID，重置并保存 AppSecret
  ② 同一页配置 IP 白名单：填**本机公网 IP**（内网 192.168.x.x 无效）
"""
import argparse
import json
import os
import struct
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zlib

BASE = "https://api.weixin.qq.com/cgi-bin"

HINTS = {
    40013: "AppID 无效（抄错了？）",
    40125: "AppSecret 无效（重置后是否保存了新的？）",
    40164: "IP 不在白名单：把报错里的 IP 加进「基本配置 → IP 白名单」",
    48001: "api unauthorized：账号没有该接口权限（未认证个人订阅号/主体不符）",
    45009: "接口调用超过限额（当天用完了，明天再试）",
    40007: "media_id 无效",
    40009: "图片尺寸/大小不符",
}


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url, timeout=30):
    with op().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_json(url, payload, timeout=60):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with op().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def solid_png(w, h, rgb=(24, 32, 48)):
    """零依赖生成纯色 PNG（探针封面用，免依赖外部图片）。"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def upload_media(token, png_bytes, filename="probe_cover.png"):
    boundary = "----wxb" + uuid.uuid4().hex
    body = b""
    body += ("--%s\r\n" % boundary).encode()
    body += ('Content-Disposition: form-data; name="media"; filename="%s"\r\n'
             % filename).encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += png_bytes + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode()
    url = ("%s/material/add_material?access_token=%s&type=image"
           % (BASE, token))
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with op().open(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fail(step, resp, extra=""):
    code = resp.get("errcode")
    msg = resp.get("errmsg", "")
    print("  ✗ %s 失败: errcode=%s errmsg=%s" % (step, code, msg))
    hint = HINTS.get(code)
    if hint:
        print("    → %s" % hint)
    if extra:
        print("    %s" % extra)
    return 1


def main():
    ap = argparse.ArgumentParser(description="公众号服务端 API 链路自检")
    ap.add_argument("--appid", default=os.environ.get("WX_APPID", ""))
    ap.add_argument("--secret", default=os.environ.get("WX_APPSECRET", ""))
    ap.add_argument("--keep", action="store_true", help="保留探针草稿（默认删掉）")
    ap.add_argument("--title", default="API 自检探针（可删）")
    args = ap.parse_args()

    if not args.appid or not args.secret:
        print("✗ 缺凭证：设置 WX_APPID / WX_APPSECRET 环境变量，或传 --appid/--secret")
        print("  取法：微信开发者平台 → 该公众号 → 基本配置（AppSecret 只显示一次）")
        return 2

    print("=" * 60)
    print("  公众号 API 链路自检")
    print("=" * 60)

    # 1. token
    print("[1/5] get_access_token")
    q = urllib.parse.urlencode({"grant_type": "client_credential",
                                "appid": args.appid, "secret": args.secret})
    r = get_json("%s/token?%s" % (BASE, q))
    if r.get("errcode"):
        return fail("get_access_token", r,
                    "常见：40164 白名单 / 40125 secret 错 / 40013 appid 错")
    token = r["access_token"]
    print("  ✓ token=%s…（有效期 %ss）" % (token[:12], r.get("expires_in")))

    # 2. 封面
    print("[2/5] material/add_material（上传探针封面 900×383）")
    up = upload_media(token, solid_png(900, 383))
    if up.get("errcode"):
        return fail("material/add_material", up, "封面接口都不通，草稿箱必然也不通")
    thumb = up["media_id"]
    print("  ✓ thumb_media_id=%s" % thumb)

    # 3. 草稿
    print("[3/5] draft/add（建探针草稿）")
    article = {
        "articles": [{
            "title": args.title,
            "author": "拆解海外自检",
            "digest": "这是一条 API 链路自检草稿，可删。",
            "content": "<p>这是一条用于验证草稿箱 API 的探针内容，可删。</p>",
            "content_source_url": "",
            "thumb_media_id": thumb,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
        }]
    }
    dr = post_json("%s/draft/add?access_token=%s" % (BASE, token), article)
    if dr.get("errcode"):
        return fail("draft/add", dr,
                    "48001 ⇒ 该账号没有草稿箱权限（未认证个人订阅号就是这一条）")
    media_id = dr["media_id"]
    print("  ✓ draft media_id=%s" % media_id)

    # 4. 回读（errcode=0 不算数）
    print("[4/5] draft/batchget（回读确认真的落库）")
    time.sleep(1)
    br = post_json("%s/draft/batchget?access_token=%s" % (BASE, token),
                   {"offset": 0, "count": 20, "no_content": 1})
    if br.get("errcode"):
        return fail("draft/batchget", br)
    ids = {it.get("media_id") for it in br.get("item", [])}
    if media_id not in ids:
        print("  ✗ 回读不到刚建的草稿（errcode=0 但没落库）")
        print("    → 按项目铁律：API 返 OK ≠ 事办成，以回读为准")
        return 1
    print("  ✓ 回读命中（草稿箱共 %d 条）" % br.get("total_count", len(ids)))

    # 5. 清理
    if args.keep:
        print("[5/5] 保留探针草稿（--keep）：请到后台草稿箱手动删")
    else:
        print("[5/5] draft/delete（清理探针草稿）")
        delr = post_json("%s/draft/delete?access_token=%s" % (BASE, token),
                         {"media_id": media_id})
        if delr.get("errcode"):
            return fail("draft/delete", delr, "到后台手动删掉这条探针稿")
        time.sleep(1)
        br2 = post_json("%s/draft/batchget?access_token=%s" % (BASE, token),
                        {"offset": 0, "count": 20, "no_content": 1})
        still = {it.get("media_id") for it in br2.get("item", [])}
        if media_id in still:
            print("  ✗ 删除接口返 OK 但草稿还在 —— 判据只看回读")
            return 1
        print("  ✓ 已删除并回读确认消失")

    print("-" * 60)
    print("结论：草稿箱全链路可用。")
    print("  ⇒ 可以把「建草稿 + 传封面」从 CDP 换成 API；")
    print("    但 freepublish / 群发仍需「认证企业主体」，最后一击仍人工。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
