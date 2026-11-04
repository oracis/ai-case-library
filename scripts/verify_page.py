"""发布后回读：用真实浏览器打开公开页，按关键词核对正文是否真的在。

为什么不用 curl：CSDN / 掘金 / 头条的文章页对 curl 返 404（WAF 假阴性），
只能看真实渲染。为什么不用 headless 截图：截图能「有内容」但看不出少了哪段。

用法：
  # 腾讯云 / 阿里云（复用 9228 的 yun profile）
  python -X utf8 scripts/verify_page.py \
      --url https://cloud.tencent.com/developer/article/2776290 \
      --expect "盯住 5 万个招聘页,ATS,幂等,去重" --title "招聘"

  # CSDN（复用 9224 的 csdn profile）
  python -X utf8 scripts/verify_page.py \
      --url https://blog.csdn.net/<user>/article/details/169850647 \
      --chrome csdn --expect "判题,沙箱"

退出码：0 全命中 / 1 有缺失 / 2 浏览器起不来。
⚠ 这是「发布成功」的唯一判据的实现：**接口返回 200 与 articleId 都不算证据**。
⚠ 关键词挑「只可能出现在新版正文里」的短语（新加的小标题、新写的结论），
  别用「技术」「产品」这种旧版也有的词，否则旧正文也能全绿（假绿）。
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

READ = r"""(function () {
  return JSON.stringify({
    href: location.href,
    title: document.title,
    text: document.body ? document.body.innerText : ''
  });
})()"""


def open_backend(which):
    """返回 (proc, cdp)。两朵云与 CSDN 各有自己的 profile 与端口。"""
    if which == "csdn":
        import csdn_set_cover as SC

        return SC.open_cdp()
    import yun_draft_cdp as Y

    return Y._connect()


def goto(cdp, url, wait):
    """打开页面：后端有 open() 就用它，否则新开 target 再 connect。"""
    if hasattr(cdp, "open"):
        cdp.open(url)
    else:
        tid = cdp.new_target(url)["id"]
        cdp.connect_target(tid)
    time.sleep(wait)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--expect", default="", help="逗号分隔，全部命中才算过")
    ap.add_argument("--title", default="", help="标题必须包含的片段")
    ap.add_argument("--chrome", default="tc", choices=["tc", "csdn"])
    ap.add_argument("--wait", type=float, default=14.0)
    ap.add_argument("--show", type=int, default=160, help="打印正文前 N 字")
    a = ap.parse_args()

    want = [w.strip() for w in a.expect.replace("，", ",").split(",") if w.strip()]

    proc = None
    try:
        proc, cdp = open_backend(a.chrome)
    except Exception as e:                                    # noqa: BLE001
        print("✗ 起不了浏览器（%s）：%r" % (a.chrome, e))
        return 2

    try:
        goto(cdp, a.url, a.wait)
        import json

        try:
            got = json.loads(str(cdp.eval(READ)), strict=False)
        except Exception:                                     # noqa: BLE001
            # eval 回的是裸对象字符串时兜底
            got = {"href": str(cdp.eval("location.href")),
                   "title": str(cdp.eval("document.title")),
                   "text": str(cdp.eval("document.body.innerText"))}

        href, title, text = got.get("href", ""), got.get("title", ""), got.get("text", "")
        print("URL  :", href[:130])
        print("标题 :", title[:110])
        print("字数 :", len(text))

        bad = []
        if a.title and a.title not in title:
            bad.append("标题缺「%s」" % a.title)
        for w in want:
            if w not in text:
                bad.append("正文缺「%s」" % w)

        # 正文被重定向到登录页/404 的形态
        if len(text) < 400:
            bad.append("正文只有 %d 字，可能是 404 / 登录墙" % len(text))

        print("前 %d 字: %s" % (a.show, text[:a.show].replace("\n", " | ")))
        if bad:
            print("\n✗ 回读不通过（%d 项）：" % len(bad))
            for b in bad:
                print("   -", b)
            return 1
        print("\n✓ 回读通过：%d 个关键词全部命中" % len(want))
        return 0
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
