# -*- coding: utf-8 -*-
"""用「改前 manifest」在影子 data 目录里跑一次真实导入，产出对照临时库。
只写 tmp/，不碰 data/ 与真库。
"""
import os
import shutil
import sys

os.environ["LEDGER_DB"] = "tmp/_verify_before_20261112.db"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import import_to_db as I  # noqa: E402
import db  # noqa: E402

SHADOW = "tmp/_shadow_data_before"
if os.path.isdir(SHADOW):
    shutil.rmtree(SHADOW)
os.makedirs(SHADOW)
for f in os.listdir("data"):
    if f.endswith(".json"):
        shutil.copy2(os.path.join("data", f), os.path.join(SHADOW, f))

TS = open("tmp/_published_at_batch_ts.txt", encoding="utf-8").read().strip()
bak = "tmp/backup_publish_manifest.json." + TS
shutil.copy2(bak, os.path.join(SHADOW, "publish_manifest.json"))
I.DATA = SHADOW

conn = db.get_conn()
try:
    I.import_platform_posts(conn)
finally:
    db.close_conn()
print("shadow import done ->", db.current_path())
