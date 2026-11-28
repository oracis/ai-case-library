import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)
import wechat_publish as wp  # noqa: E402
import wechat_sync as ws  # noqa: E402

wp._autostart_chrome_if_needed(port=9222)
cdp = wp.CDP(9222)
_tid, tok = wp._connect_mp(cdp)
if not tok:
    raise SystemExit("✗ no token")
pubs, ok = ws.fetch_published_remote(cdp, tok)
print("fetch_ok=%s total=%d" % (ok, len(pubs)))
for key in ("Chatsell", "Freedoom", "freedoom", "游戏厅"):
    hits = [p for p in pubs if key in (p.get("title") or "")]
    print("KEY", key, "hits", len(hits))
    for p in hits[:5]:
        print("  ", {k: p.get(k) for k in ("title", "appmsg_id", "appmsgid",
                                          "update_time", "create_time")})
