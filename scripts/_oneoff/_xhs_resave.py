"""小红书：把 16 个（wpconvert 算 2 张）金额违规 case 重发成干净草稿。

流程：先 build（9333 无头 Chrome 渲染卡片 + 重生干净 note.json），再 publish
（9223 已登录 Chrome 存草稿）。两个阶段分开，避免跨端口抢资源。

每个 case 的 build / publish 各自跑独立子进程 + 硬超时（卡死就杀，不传染）。
不重试 publish（publish 末尾点「存草稿」后才可能卡，重试会造重复干净稿；
最后用 _xhs_verify.py 复核实际落箱情况，缺哪条手动补）。

前提：v3 删除脚本已把 17 张脏稿删光、9223 仍登录小红书。

用法：python3 -u scripts/_xhs_resave.py
"""
import sys, os, time, subprocess
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
XHS = os.path.join(ROOT, "scripts", "xhs_publish.py")
PY = sys.executable
ENV = dict(os.environ)
ENV.update({"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"})

# wpconvert 只算一个 case（两条脏稿删光后重发一次即可）
CASES = ['augora-ai', 'boothie-ai-photo-editor', 'downconvert-sketchup-files',
         'fastwrite-io', 'growingupvideo', 'homy', 'macwall', 'nutria-9e3eaa',
         'pet-plus-us', 'publbee', 'quotedeck', 'stealth-rdp', 'vectosolve',
         'wagiff-app', 'white-glove-content',
         'wpconvert-ai-convert-ai-sites-to-wordpress']

BUILD_TO = 360
PUB_TO = 480


def run(args, timeout):
    t0 = time.time()
    try:
        p = subprocess.run([PY, XHS] + args, cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True, env=ENV)
        dt = time.time() - t0
        return p.returncode, p.stdout, p.stderr, dt
    except subprocess.TimeoutExpired:
        return -9, "", "TIMEOUT after %ds" % timeout, timeout


def tail(s, n=600):
    s = s or ""
    return s[-n:]


def main():
    results = {}
    # ---- 阶段 1：build ----
    print("######## 阶段1：build %d 个 case（9333 无头）########" % len(CASES),
          flush=True)
    for cid in CASES:
        print("--- build %s ---" % cid, flush=True)
        rc, out, err, dt = run(["build", "--case", cid], BUILD_TO)
        if rc != 0:
            print("  ✗ BUILD FAIL rc=%d (%.0fs): %s" % (rc, dt, tail(err or out)),
                  flush=True)
            results[cid] = "build_fail"
        else:
            print("  ✓ build ok (%.0fs)" % dt, flush=True)
            results[cid] = "built"

    # ---- 阶段 2：publish（仅 build 成功的）----
    todo = [c for c in CASES if results.get(c) == "built"]
    print("\n######## 阶段2：publish %d 个 case（9223 登录态）########"
          % len(todo), flush=True)
    for cid in todo:
        print("--- publish %s ---" % cid, flush=True)
        rc, out, err, dt = run(["publish", "--case", cid], PUB_TO)
        if rc != 0:
            print("  ✗ PUBLISH FAIL rc=%d (%.0fs): %s"
                  % (rc, dt, tail(err or out)), flush=True)
            results[cid] = "publish_fail"
        else:
            print("  ✓ publish ok (%.0fs)" % dt, flush=True)
            results[cid] = "ok"

    print("\n==== 汇总 ====", flush=True)
    cc = Counter(results.values())
    for k, v in cc.items():
        print("  %s: %d" % (k, v))
    for cid in CASES:
        print("  %-42s %s" % (cid, results.get(cid)))


if __name__ == "__main__":
    main()
