"""Diagnostic: where does publish_one stall? Logs each step to a file (no tail
buffering). Opens TT_EDITOR for ONE case and types the title only, reporting how
far it gets. Run with output redirected to scripts/_diag.log."""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toutiao_publish as tp

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_diag.log")


def log(*a):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%.1f " % time.time() + " ".join(str(x) for x in a) + "\n")


log("start; TT_EDITOR=", tp.TT_EDITOR, "TT_DRAFT=", tp.TT_DRAFT)
cdp, t = tp._open(tp.TT_EDITOR, wait=10)
log("opened target", t.get("id"))
try:
    log("check_login...")
    tp._check_login(cdp)
    log("login ok")
    sel = tp.SEL.get("title")
    log("title selector:", sel)
    has = cdp.eval("(function(){return document.querySelector(%s)?1:0;})()" % __import__("json").dumps(sel))
    log("title box present:", has)
    log("url:", cdp.eval("document.location.href"))
except Exception as e:
    log("EXC", type(e).__name__, e)
finally:
    try:
        cdp.close_target(t["id"])
    except Exception:
        pass
    log("done")
