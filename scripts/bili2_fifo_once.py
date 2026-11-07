#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""二号 FIFO 一次跑：起 9224 浏览器 → `publish_one --platform bilibili2 --yes`
→ 回读（已发表列表 / 两本台账）→ 打印结论。

为什么要一个脚本把「起浏览器」也包进来（2026-11-06 实测）
------------------------------------------------------
**本机后台任务/父命令一结束，整组进程被回收**：用 `_launch_cdp.py --hold 5400`
起的 Chrome 在命令退出后 30 秒内就 10061，`--hold` 也保不住（hold 撑的是那条
命令本身，不是 Chrome）。所以「起浏览器 → 发布 → 收工」必须在**同一个前台
进程**里做完，本脚本就是那条命令。

判据（没跑通不许写成已发）
--------------------------
* 发布成功只认 `publish_one` 的退出码 **加上**「已发表列表按 aid/标题回读命中」
  —— 后者由 `ui_publish_draft()` 内部做完才写账，脚本这里再核一遍两本台账。
* 二号的真账在**游戏仓** `publish_state.json`（`games_state` 唯一读写口），
  本仓 db 是 `platform_posts(platform='bilibili2')`；两本都读，缺一本就报。
* 配额 1 篇/天：当日已发时 `publish_one` 会直接拒绝，脚本原样转述，**不绕**。

用法
----
    python -X utf8 scripts/bili2_fifo_once.py --dry     # 只看要做什么（默认）
    python -X utf8 scripts/bili2_fifo_once.py --go      # 真发

⚠ `--go` 会**真的发布一篇**，不可撤回。
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PY = sys.executable
PROFILE = os.path.expandvars(r"%USERPROFILE%\chrome-bili2-profile")
PORT = 9224


def port_alive(port):
    """CDP 端口是否在监听（2s 硬超时，绕开系统代理）。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        op.open("http://127.0.0.1:%d/json/version" % port, timeout=2).read(64)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def ensure_cdp():
    """起/复用 9224，返回 (ok, 说明)。"""
    import chrome_cdp_launch as L
    L.clear_proxy()
    if port_alive(PORT):
        return True, "已在运行，复用"
    L.remove_lock(PROFILE)
    chrome, label = L.pick_chrome()
    if not chrome:
        return False, "找不到 Chrome"
    argv = L.build_args(chrome, PROFILE, port=PORT)
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, close_fds=True,
                            creationflags=0x00000008)   # DETACHED_PROCESS
    if not L.wait_cdp(proc, timeout=45, port=PORT):
        return False, "9224 启动失败（Chrome exit=%s）" % proc.poll()
    return True, "已常驻（%s）" % label


def readback(before_ids):
    """回读两本账 + 今日档期日志，挑出**这次新增**的那条。"""
    import publish_slot as SLOT
    import games_state as GS
    today = SLOT.today()
    rows = [r for r in SLOT.records("bilibili2") if r[0].startswith(today)]
    new = [r for r in rows if r[1] not in before_ids]
    print("\n[回读] 今日 bilibili2 台账 %d 条（并集：本仓 db ∪ 游戏仓）" % len(rows))
    for r in rows:
        print("       %s" % (r,))
    if not new:
        print("       ⚠ 没有新增记录 —— 发布**未生效**（别当成已发）")
        return False
    cid = new[-1][1]
    # 归一用 games_state.norm（`ext-x-b2` / `ext-x` / `x` 三者等价）——
    # 并集读口给出的可能是 `ext-ep-003-freedoom`（游戏仓写法，无 -b2），
    # 手写 startswith/endswith 剥会漏，早先这里就把它当成「查不到」。
    slug = GS.norm(cid)
    hit = [e for e in GS.entries("bilibili2") if GS.norm(e.get("case")) == slug]
    state = hit[-1].get("state") if hit else None
    print("       新增：%s ｜ 游戏仓 state=%s ｜ remote_id=%s"
          % (cid, state, (hit[-1].get("remote_id") if hit else None)))
    if state != "published":
        print("       ⚠ 游戏仓台账没记成 published（#78 的补写没落地），查日志别重发")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="二号 FIFO 一次跑（起浏览器 + 发布 + 回读）")
    ap.add_argument("--go", action="store_true", help="真发（默认干跑）")
    a = ap.parse_args()

    import publish_slot as SLOT
    d = SLOT.decide("bilibili2")
    if not d.get("kind"):
        print("✗ 今天不发/已到上限：%s" % (d.get("blocked") or d.get("reason")))
        return 1

    before = {r[1] for r in SLOT.records("bilibili2")}
    if not a.go:
        print("（干跑）配额 OK；将执行：起 9224 → publish_one --platform bilibili2 --yes")
        print("         今日已有 bilibili2 记录 %d 条" % len(before))
        return 0

    ok, why = ensure_cdp()
    print("[cdp] %s" % why, flush=True)
    if not ok:
        return 1

    env = dict(os.environ,
               CDP_BILI_PORT=str(PORT), CDP_PLAT_PROFILE=PROFILE,
               NO_PROXY="*", no_proxy="*")
    print("\n[发布] publish_one --platform bilibili2 --yes", flush=True)
    rc = subprocess.call([PY, "-X", "utf8",
                          os.path.join(HERE, "publish_one.py"),
                          "--platform", "bilibili2", "--yes"], cwd=ROOT, env=env)
    print("\n[发布] rc=%d" % rc, flush=True)
    time.sleep(3)
    good = readback(before)
    if rc == 0 and good:
        print("\n✓ 二号 FIFO 首跑完成（已发表列表回读 + 游戏仓台账双命中）")
        return 0
    print("\n✗ 没跑通：先按标题查已发表列表，别原地重跑（B站日上限 1 篇）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
