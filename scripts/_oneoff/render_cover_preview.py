# -*- coding: utf-8 -*-
"""把修过文案的封面渲染到 tmp/cover_preview/ 目视核对（不动 out/ 下的正式产物）。"""
import base64
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import toutiao_publish as tp  # noqa: E402
import wechat_publish as wp  # noqa: E402

OUT = os.path.join(ROOT, "tmp", "cover_preview")
IDS = ["storyshort", "pet-plus-us", "abmaxx", "autocontent-api", "angel-match"]


def main():
    os.makedirs(OUT, exist_ok=True)
    cs = {c["id"]: c for c in tp.load_cases()}
    for cid in IDS:
        c = cs[cid]
        html = tp.tt_cover_html(c)
        url = "data:text/html;charset=utf-8;base64," + base64.b64encode(
            html.encode("utf-8")).decode("ascii")
        browser = sub = tid = None
        try:
            browser = wp.CDP(9223)
            tid = browser.new_target(url)["id"]
            sub = wp.CDP(9223)
            sub.connect_target(tid)
            sub.send("Emulation.setDeviceMetricsOverride",
                     {"width": tp.TT_COVER_W, "height": tp.TT_COVER_H,
                      "deviceScaleFactor": 1, "mobile": False})
            time.sleep(1.0)
            sub.eval(wp.x.AUTOFIT_JS if hasattr(wp, "x") else "1",
                     refresh_context=True) if False else None
            r = sub.send("Page.captureScreenshot", {
                "format": "png", "captureBeyondViewport": True,
                "clip": {"x": 0, "y": 0, "width": tp.TT_COVER_W,
                         "height": tp.TT_COVER_H, "scale": 2}})
            data = (r.get("result") or {}).get("data", "")
            p = os.path.join(OUT, "%s.png" % cid)
            with io.open(p, "wb") as f:
                f.write(base64.b64decode(data))
            print("%-22s %-40s -> %s" % (cid, wp.cover_desc(c)[:38], p))
        finally:
            for obj in (sub, browser):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:                       # noqa: BLE001
                    pass
            if tid and browser is not None:
                try:
                    browser.close_target(tid)
                except Exception:                       # noqa: BLE001
                    pass


if __name__ == "__main__":
    main()
