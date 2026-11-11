"""重建头条横版封面 out/toutiao/<cid>/cover.png（2026-10-15）。

背景：cover_desc 数据层修完后（commit 204c90d），XHS 卡片 / 头条 meta /
B站 article 都重建了，但 cover.png 还是旧版（Oct 2-13，印着旧封面文案）。
cover.png 是**头条题图与 B站封面的共同源图**：
  - toutiao_publish.cover_path() 优先 out/toutiao/<cid>/cover.png，
    退回 XHS card-1；
  - bilibili_publish.cover_path() 直接读它。
⇒ 不重建它，远端删旧发新会把旧图原样再传一遍。

渲染走 9333 一次性无头 Chrome（xp.ensure_chrome，临时 profile），
不碰 9223 登录态。逐条 subprocess：单条崩只影响一条
（照 bili_cover_repair 的教训——cover --all 一次跑完会被环境回收）。

用法：python3 -u scripts/_tt_cover_regen.py <cid> [<cid>…]
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
PY = sys.executable
TT = os.path.join(ROOT, "scripts", "toutiao_publish.py")
PORT = "9333"


def main():
    cids = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not cids:
        print("用法：_tt_cover_regen.py <cid> [<cid>…]")
        return 2
    import xhs_publish as xp
    xp.ensure_chrome(int(PORT))          # 已起则复用，没起就起一个
    ok = 0
    for i, cid in enumerate(cids, 1):
        try:
            p = subprocess.run([PY, TT, "covers", "--case", cid, "--port", PORT],
                               cwd=ROOT, capture_output=True, text=True, timeout=300)
            out = (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            out = "TIMEOUT 300s"
        good = "→ ok" in out
        ok += 1 if good else 0
        print("[%d/%d] %s → %s" % (i, len(cids), cid, "ok" if good else "失败"),
              flush=True)
        if not good:
            print(out[-600:], flush=True)
    print("完成 %d/%d" % (ok, len(cids)))
    return 0 if ok == len(cids) else 2


if __name__ == "__main__":
    sys.exit(main())
