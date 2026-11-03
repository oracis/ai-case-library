#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份 `data/ledger.db` 到 `_backups/`，带时间戳，保留最近 N 份。

用法::

    python scripts/backup_ledger.py              # 备份一次，保留最近 10 份
    python scripts/backup_ledger.py --keep 20    # 保留最近 20 份

依赖：仅 Python 标准库。
"""

import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
BACKUP_DIR = os.path.join(ROOT, "_backups")
DB_PATH = os.path.join(DATA, "ledger.db")


def backup(keep=10):
    if not os.path.isfile(DB_PATH):
        print(f"⚠ 数据库文件不存在：{DB_PATH}，跳过备份")
        return 0

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"ledger.db.bak-{ts}")
    shutil.copy2(DB_PATH, dst)
    print(f"✓ 已备份：{dst}")

    # 清理超出 keep 份的旧备份
    backups = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith("ledger.db.bak-") and f.endswith(".db")
    )
    while len(backups) > keep:
        old = os.path.join(BACKUP_DIR, backups.pop(0))
        os.remove(old)
        print(f"  已清理旧备份：{os.path.basename(old)}")

    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="备份 SQLite 统一台账 ledger.db")
    ap.add_argument("--keep", type=int, default=10, help="保留最近多少份备份（默认 10）")
    args = ap.parse_args()
    return backup(keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
