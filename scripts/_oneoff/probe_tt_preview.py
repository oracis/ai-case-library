#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针：`preview_article/?pgc_id=` 预览页能不能读到草稿全文（只读）。

`draft_list` 每条 `graphic_extra.article_url` 指向这个地址，且带
`content_word_cnt`（服务端记的正文字数）⇒ 拿它当独立回读基准。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import wechat_publish as wp                                   # noqa: E402
import toutiao_publish as tt                                  # noqa: E402

GID = sys.argv[1] if len(sys.argv) > 1 else "7696901580452348454"
URL = (sys.argv[2] if len(sys.argv) > 2
       else "https://mp.toutiao.com/preview_article/?pgc_id=%s" % GID)

JS = """(function(){
  return JSON.stringify({
    url: location.href,
    len: (document.body.innerText||'').length,
    head: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,220),
    mark: (document.body.innerText||'').indexOf('我去核了一遍')>=0
  });
})()"""


def main():
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY", "ftp_proxy", "FTP_PROXY"):
        os.environ.pop(k, None)
    wp.ensure_no_proxy_for_localhost()
    wp._autostart_chrome_if_needed(port=tt.CDP_PORT)
    cdp = wp.CDP(tt.CDP_PORT)
    t = cdp.new_target(URL)
    print("tab", t["id"], flush=True)
    print("connect", cdp.connect_target(t["id"]), flush=True)
    for i in range(8):
        time.sleep(2)
        try:
            raw = cdp.eval(JS, refresh_context=True)
        except Exception as e:                                 # noqa: BLE001
            print("  [%d] eval ERR %s" % (i, e), flush=True)
            continue
        print("  [%d] %s" % (i, str(raw)[:320]), flush=True)
    # 从浏览器 Target 列表独立回读 URL（不依赖页面执行上下文）
    for x in cdp.list_targets():
        if x.get("id") == t["id"]:
            print("  [target] url=%s" % (x.get("url") or "")[:130], flush=True)
    cdp.close_target(t["id"])


if __name__ == "__main__":
    main()
