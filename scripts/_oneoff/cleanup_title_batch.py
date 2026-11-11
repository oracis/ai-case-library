# -*- coding: utf-8 -*-
"""PRD #123 待办②：清「aid= / draftId=」类垃圾 title（db + manifest 定点）。"""
import json, os, shutil, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import db  # noqa: E402

LEDGER = db.DB_PATH
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
TITLE_CODDEX = "交互式编程课程的运行时设计：代码执行沙箱、事件溯源进度与内容权限门控"
TITLE_CHESS_TC = "浏览器端棋类分析引擎的工程取舍：WASM 引擎、PGN 增量解析与对局状态同步"
TITLE_CHESS_ALI = "Chessigma：百万用户级实时对弈的负载形状与算力成本"

shutil.copy(LEDGER, os.path.join(ROOT, "tmp", "ledger_backup_title_cleanup_20261109.db"))
shutil.copy(MANIFEST, os.path.join(ROOT, "tmp", "publish_manifest_before_title_cleanup_20261109.json"))
print("备份: tmp/ledger_backup_title_cleanup_20261109.db")
print("      tmp/publish_manifest_before_title_cleanup_20261109.json")

# ---- 1) db：cod-dex/aliyun 的 published 行 title 纠偏 ----
conn = db.get_conn()
try:
    cur = conn.cursor()
    cur.execute("SELECT title, note FROM platform_posts "
                "WHERE case_id='cod-dex' AND platform='aliyun' AND remote_id='1778138'")
    old = cur.fetchone()
    assert old and old[0] == "aid=1701241", "前置不符: %s" % (old and old[0])
    add = (" ‖ [11-09 #123 清垃圾值] title 由解析残留 aid=1701241 回填为**线上公开页真标题**。"
           "证据：匿名 GET https://developer.aliyun.com/article/1778138 → HTTP 200 / 70684B，"
           "<title> 与 <h1> 均为「%s」，无审核横幅；同 case 腾讯云 published 行 title 逐字相同，"
           "两处互证。只改台账 title，平台侧零写操作，state=published / remote_id / url / published_at 全不动。"
           "⛔ 本地 drafts/newbatch/aliyun/cod-dex.md 的 h1 是另一版标题，不用来覆盖线上标题。" % TITLE_CODDEX)
    cur.execute("UPDATE platform_posts SET title=?, note=?, "
                "updated_at=datetime('now','localtime') "
                "WHERE case_id='cod-dex' AND platform='aliyun' AND remote_id='1778138'",
                (TITLE_CODDEX, (old[1] or "") + add))
    assert cur.rowcount == 1
    conn.commit()
    print("\n[db] cod-dex/aliyun: aid=1701241 → %s (rowcount=1)" % TITLE_CODDEX[:36])
finally:
    db.close_conn()

# ---- 2) manifest：文本级定点替换，零格式扰动 ----
raw = open(MANIFEST, "r", encoding="utf-8", newline="").read()
pairs = [
    ('"title": "aid=1700270",', '"title": %s,' % json.dumps(TITLE_CHESS_ALI, ensure_ascii=False)),
    ('"title": "draftId=338751",', '"title": %s,' % json.dumps(TITLE_CHESS_TC, ensure_ascii=False)),
    ('"title": "aid=1701241",', '"title": %s,' % json.dumps(TITLE_CODDEX, ensure_ascii=False)),
    ('"title": "draftId=338752",', '"title": %s,' % json.dumps(TITLE_CODDEX, ensure_ascii=False)),
    # chessigma/aliyun 还指向 #111 已删旧稿 1700270，按 db 真值同步
    ('"draft_id": "1700270",', '"draft_id": "1711205",'),
    ('"url": "https://developer.aliyun.com/article/new?edit=1700270#/"',
     '"url": "https://developer.aliyun.com/article/new?edit=1711205#/"'),
    ('"state": "published",\n   "title": %s,' % json.dumps(TITLE_CHESS_ALI, ensure_ascii=False),
     '"state": "draft",\n   "title": %s,' % json.dumps(TITLE_CHESS_ALI, ensure_ascii=False)),
]
for old_s, new_s in pairs:
    n = raw.count(old_s)
    assert n == 1, "替换串命中 %d 次（应为 1）: %s" % (n, old_s[:50])
    raw = raw.replace(old_s, new_s, 1)
    print("[manifest] %s → %s" % (old_s[:46], new_s[:46]))

d = json.loads(raw)  # 合法性先验
tmp = MANIFEST + ".tmp"
with open(tmp, "w", encoding="utf-8", newline="\n") as f:
    f.write(raw)
os.replace(tmp, MANIFEST)
b = open(MANIFEST, "rb").read()
print("\n[manifest] 写回：CRLF=%d LF=%d 尾=%r size=%d" % (
    b.count(b"\r\n"), b.count(b"\n"), b[-3:], len(b)))

# ---- 3) 验收 ----
print("\n=== 验收 ===")
conn = db.get_conn()
try:
    cur = conn.cursor()
    cur.execute("SELECT case_id,platform,title FROM platform_posts "
                "WHERE title LIKE 'aid=%' OR title LIKE 'draftId=%' OR title LIKE 'articleId=%'")
    print("db 残留垃圾 title:", [dict(r) for r in cur.fetchall()] or "无")
    cur.execute("SELECT case_id,platform,title FROM platform_posts "
                "WHERE case_id IN ('chessigma','cod-dex') AND platform IN ('aliyun','tencentcloud') "
                "ORDER BY case_id,platform")
    for r in cur.fetchall():
        print("  %-10s %-13s %s" % (r[0], r[1], r[2][:52]))
finally:
    db.close_conn()

bad = [(c, p, x.get("title")) for c, ps in d.items() if isinstance(ps, dict)
       for p, x in ps.items() if isinstance(x, dict)
       and str(x.get("title", "")).startswith(("aid=", "draftId=", "articleId="))]
print("manifest 残留垃圾 title:", bad or "无")

from multiplatform.state import Manifest  # noqa: E402
m = Manifest(MANIFEST)
for c, p in (("chessigma","aliyun"),("chessigma","tencentcloud"),
             ("cod-dex","aliyun"),("cod-dex","tencentcloud")):
    rec = (m.data.get(c) or {}).get(p) or {}
    print("  Manifest.load %-10s %-13s state=%-9s title=%s" % (
        c, p, rec.get("state"), str(rec.get("title"))[:44]))
