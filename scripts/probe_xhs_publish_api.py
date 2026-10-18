#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书「发布接口」逆向探测（2026-10-18）。

## 已确认的链路（2026-10-18 实测，逐条标注状态）

    1) GET  https://edith.xiaohongshu.com/api/media/v1/upload/creator/permit
            ?version=1&source=web&biz_name=spectrum&scene=image&file_count=N
        签名：GET 签 pathname+search（见 xhs_webapi）
        → data.uploadTempPermits[0] = {fileIds, token, uploadAddr, bucket}
        ✅ 已跑通（HTTP 200，拿到 uploadAddr / token / fileId）
        ⚠ biz_name 不能省：省了会返 `bizCloudConfig-list-empty`（code 0 但没通道）
    2) 二进制上传：**不是裸 HTTP，是腾讯云 COS SDK**
        bundle 原文：`new COS({Domain:r.uploadAddr, getAuthorization: …
        TmpSecretId:r.secretId||"null", TmpSecretKey:r.secretKey||"null",
        SecurityToken:r.token …})`
        ❌ 未实现。直接打 ros-upload 的两种错法都试过：
           裸 body → `request Content-Type isn't multipart/form-data`
           multipart → `{"code":9,"msg":"invalid param"}`
        本项目图片上传本来走 DOM 选文件，不是瓶颈，先不啃 COS 签名。
    3) POST https://edith.xiaohongshu.com/web_api/sns/v2/note   ← 创建/发布笔记
        ✅ 签名规则已破：POST 的 data 参数**必须传对象**（传字符串稳定 406）
        ❌ 服务端回 **HTTP 461 + `verifytype: 124` + `verifyuuid: …`** = 要过验证码。
           body 却是 `{"code":0,"success":true}` —— 光看 body 会误以为发布成功了，
           这是本文档最值钱的一条坑。
    4) PUT  https://edith.xiaohongshu.com/web_api/sns/capa/postgw/note/update   ← 改已有笔记

⇒ **结论：发布（写）这条路止步于风控，纯 API 发不出去；读侧 + 上传许可可 API 化。**
  别再花时间调 payload —— 461 与 body 无关（空 body 和完整 body 返回完全一样）。

## 参数是「问出来的」，不是猜的

微信式文档不存在，但服务的错误信息非常具体（`required param check: file_count`），
所以探测法是**逐个补必填项**：每次少一个参数就报出下一个，迭代几轮就凑齐。
本脚本的 `--permit` 就是在演示这条路子。

## 用法

    python -X utf8 scripts/probe_xhs_publish_api.py --permit          # 只要上传许可
    python -X utf8 scripts/probe_xhs_publish_api.py --upload <图片>    # 许可 + 试上传
    python -X utf8 scripts/probe_xhs_publish_api.py --try-post        # 试发布，读风控码
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import xhs_webapi as X  # noqa: E402

EDITH = "https://edith.xiaohongshu.com"
PERMIT = EDITH + "/api/media/v1/upload/creator/permit"
POST_NOTE = EDITH + "/web_api/sns/v2/note"
UPDATE_NOTE = EDITH + "/web_api/sns/capa/postgw/note/update"

# 460/461/471 是小红书非标准的风控类状态码，461/471 会带 verifytype/verifyuuid 头
NEED_VERIFY = (460, 461, 471)


def try_post_note(cdp, payload=None):
    """打一次「创建笔记」接口，把「看着成功其实是风控」的情况说清楚。

    ⚠ 别循环刷：每次触发都会把风险分抬高（2026-10-18 连打 5 次，
      返回码始终 461，说明**与 body 无关**，是请求级闸门）。
    """
    body = payload or {"common": {}, "note": {}}
    st, js, raw, headers = X.fetch_full(cdp, POST_NOTE, "POST", body)
    vt = (headers or {}).get("verifytype")
    vu = (headers or {}).get("verifyuuid")
    print("HTTP %s" % st)
    print("  body      : %s" % str(raw)[:160])
    if vt or vu:
        print("  风控头    : verifytype=%s verifyuuid=%s" % (vt, vu))
    if st in NEED_VERIFY:
        print("  ⇒ 风控拦截（verifytype=%s）：需要过验证码。纯 API 发布到此为止，"
              "页面上点「发布」靠真人交互才过得了。" % vt)
        return 0
    if st == 200 and isinstance(js, dict) and js.get("code") == 0:
        print("  ⚠ 真发出去了！data=%s"
              % json.dumps(js.get("data"), ensure_ascii=False)[:200])
        return 0
    return 2


def get_permit(cdp, count=1, biz="spectrum", scene="image"):
    """取上传许可。返回 permit dict（含 fileIds/token/uploadAddr）。"""
    url = "%s?version=1&source=web&biz_name=%s&scene=%s&file_count=%d" % (
        PERMIT, biz, scene, count)
    st, js, raw = X.fetch_json(cdp, url, "GET")
    if st != 200 or not isinstance(js, dict) or js.get("code") != 0:
        raise RuntimeError("permit 失败：HTTP %s / %s" % (st, raw[:300]))
    permits = ((js.get("data") or {}).get("uploadTempPermits") or [])
    if not permits:
        # 常见原因：biz_name 缺失 → result.message 里会写 bizCloudConfig-list-empty
        raise RuntimeError("permit 没返回可用通道：%s" % json.dumps(js)[:300])
    return permits[0]


def _multipart(field, filename, data, mime="image/png"):
    boundary = "----xhsprobe" + os.urandom(8).hex()
    head = ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
            "Content-Type: %s\r\n\r\n" % (boundary, field, filename, mime)).encode()
    tail = ("\r\n--%s--\r\n" % boundary).encode()
    return head + data + tail, "multipart/form-data; boundary=%s" % boundary


def upload_file(permit, path, timeout=120):
    """把本地文件传到 ros-upload，返回 (file_id, status, 回包片段)。

    ⚠ 必须 multipart/form-data（2026-10-18 实测）：直接裸 body 会被拒
      `{"code":-1,"msg":"request Content-Type isn't multipart/form-data"}`。
    """
    fid = permit["fileIds"][0]
    addr = permit.get("uploadAddr") or "ros-upload.xiaohongshu.com"
    url = "https://%s/%s" % (addr, fid)
    raw = open(path, "rb").read()
    body, ctype = _multipart("file", os.path.basename(path), raw)
    req = urllib.request.Request(url, data=body, method="POST")
    # ⚠ 不加 ProxyHandler({}) 的话，系统代理会把内网直连也代走，表现为超时
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req.add_header("Authorization", permit["token"])
    req.add_header("Content-Type", ctype)
    try:
        with op.open(req, timeout=timeout) as r:
            return fid, r.status, r.read()[:300].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return fid, e.code, e.read()[:300].decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--permit", action="store_true", help="只取上传许可")
    ap.add_argument("--upload", metavar="IMG", help="许可 + 真上传一张图")
    ap.add_argument("--try-post", action="store_true",
                    help="打一次创建笔记接口，读风控码（不会真发出笔记）")
    ap.add_argument("--count", type=int, default=1)
    args = ap.parse_args()

    cdp, page = X.connect()
    if cdp is None:
        print("✗ 连不上小红书页面（先跑 scripts/_launch_9223.py --hold 28800）")
        return 1
    print("页面：%s" % (page.get("url") or "")[:90])

    if args.try_post:
        return try_post_note(cdp)

    permit = get_permit(cdp, count=args.count)
    print("✓ permit：fileIds=%s" % permit["fileIds"])
    print("          uploadAddr=%s token=%s…"
          % (permit.get("uploadAddr"), (permit.get("token") or "")[:18]))
    if args.permit and not args.upload:
        return 0

    if args.upload:
        fid, status, txt = upload_file(permit, args.upload)
        print("→ upload HTTP %s" % status)
        print("  file_id = %s" % fid)
        print("  回包：%s" % txt[:200])
        return 0 if status in (200, 201, 204) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
