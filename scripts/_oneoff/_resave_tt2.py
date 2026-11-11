"""头条重存 v2（健壮版）。

问题复盘（2026-10-14）：
- 旧脚本 _resave_tt.py 在 cmd_dedup 的 `location.reload()` + 重数草稿 那一步
  偶发卡死（页面导航后执行上下文失效，CDP eval 收不到回包），且 WS 超时只有
  30s，正文 insertHTML 这种慢 eval 会冒 socket.timeout 把 publish_one 打挂。
- 本版：
  ① 每个目标放进独立子进程，硬超时（默认 360s）一到就 terminate，绝不会无限挂；
  ② 建干净草稿走 publish_one（WS 超时已放宽到 90s）；
  ③ 删脏草稿**不再 reload/重数**，直接「点删除 → 等确认框 → 点确定」后关页，
     脏稿用金额子串定位（MRR / $1,808 / 250 欧），不会误删干净稿。
"""
import sys
import os
import time
import json
import multiprocessing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toutiao_publish as tp
from toutiao_publish import (
    _count_dup_and_delbtn, _confirm_delete, _open, TT_DRAFT, _check_login,
)

CASES = {c["id"]: c for c in tp.load_cases()}

# cid -> (是否需要建干净草稿, 脏稿金额子串)
TARGETS = {
    "nutria-9e3eaa": (True,  "MRR"),
    "easymix":       (True,  "250 欧"),
    "vectosolve":    (False, "$1,808"),   # 干净稿已建，这里只负责清脏稿
}

TIMEOUT = float(os.environ.get("RESVE_TIMEOUT", "360"))


def publish_clean(cid):
    c = CASES[cid]
    print("[%s] publish_one（建干净草稿）..." % cid, flush=True)
    for attempt in range(1, 3):
        try:
            ok = tp.publish_one(c, dry=False, yes=False, cover=True)
            print("    publish_one -> %s (attempt %d)" % (ok, attempt), flush=True)
            if ok:
                return True
            if attempt == 1:
                print("    [retry] 首次未成功，再试一次", flush=True)
                time.sleep(3)
        except Exception as e:
            print("    [publish exception] %s: %s" % (type(e).__name__, e), flush=True)
            if attempt == 1:
                print("    [retry] 异常后重试", flush=True)
                time.sleep(3)
            else:
                return False
    return False


def delete_dirty(amt_substr):
    print("  delete_dirty（金额子串=%r）..." % amt_substr, flush=True)
    cdp, t = _open(TT_DRAFT, wait=8)
    try:
        _check_login(cdp)
        deleted = 0
        for i in range(3):
            n, pos = _count_dup_and_delbtn(cdp, amt_substr)
            print("    匹配 %d 条，删除按钮坐标=%s" % (n, pos), flush=True)
            if n <= 0 or not pos:
                break
            if not _confirm_delete(cdp, pos):
                print("    [warn] 没点到确认框，停", flush=True)
                break
            deleted += 1
            time.sleep(1.5)
        print("    已删 %d 条（子串 %r）" % (deleted, amt_substr), flush=True)
        return deleted
    except Exception as e:
        print("    [delete exception] %s: %s" % (type(e).__name__, e), flush=True)
        return -1
    finally:
        try:
            cdp.close_target(t["id"])
        except Exception:
            pass


def run_one(cid, need_publish, amt_substr, result):
    try:
        if need_publish:
            publish_clean(cid)
        deleted = delete_dirty(amt_substr)
        result[cid] = {"publish": need_publish, "deleted": deleted,
                       "status": "ok"}
    except Exception as e:
        result[cid] = {"status": "exception", "err": "%s: %s" % (type(e).__name__, e)}
    print("[%s] DONE" % cid, flush=True)


def main():
    mgr = multiprocessing.Manager()
    result = mgr.dict()
    procs = {}
    for cid, (need_pub, amt) in TARGETS.items():
        p = multiprocessing.Process(target=run_one,
                                    args=(cid, need_pub, amt, result))
        p.start()
        procs[cid] = p
    for cid, p in procs.items():
        p.join(TIMEOUT)
        if p.is_alive():
            print("[%s] 超时 %ds，强制终止" % (cid, int(TIMEOUT)), flush=True)
            p.terminate()
            p.join(10)
            result[cid] = {"status": "timeout"}
    print("==== 汇总 ====", flush=True)
    print(json.dumps(dict(result), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
