#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探测：本机 Chrome profile 里有没有 modelscope 登录态 cookie。

不写任何文件、不起浏览器。只统计 cookie 的 name（值不解密）。
"""
import os
import shutil
import sqlite3
import tempfile

# Git Bash 里 LOCALAPPDATA 可能缺失，硬编码兜底
LOCAL = os.environ.get("LOCALAPPDATA") or r"C:\Users\DELL\AppData\Local"
PROFILES = [
    ("CDP专用", os.path.join(LOCAL, "Google", "ChromeCDP")),
    ("日常Chrome", os.path.join(LOCAL, "Google", "Chrome", "User Data")),
]
COOKIE_REL = os.path.join("Default", "Network", "Cookies")


def dump(profile):
    db = os.path.join(profile, COOKIE_REL)
    if not os.path.exists(db):
        return None, "库不存在: %s" % db
    tmp = os.path.join(tempfile.gettempdir(), "ms_ck_probe.db")
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    try:
        try:
            shutil.copy2(db, tmp)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            try:
                with open(db, "rb") as f:
                    data = f.read()
                with open(tmp, "wb") as f:
                    f.write(data)
            except OSError:
                src = sqlite3.connect("file:%s?mode=ro"
                                      % db.replace("\\", "/"), uri=True)
                try:
                    dst = sqlite3.connect(tmp)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
        conn = sqlite3.connect(tmp)
        try:
            rows = conn.execute(
                "select host_key, name from cookies "
                "where host_key like '%modelscope%' "
                "or name like '%modelscope%' or name like '%magicube%'"
            ).fetchall()
            total = conn.execute("select count(*) from cookies").fetchone()[0]
        finally:
            conn.close()
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return (total, rows), None


def main():
    for label, p in PROFILES:
        print("=" * 60)
        print("[%s] %s" % (label, p))
        res, err = dump(p)
        if err:
            print("  %s" % err)
            continue
        total, rows = res
        print("  cookie 总数 = %d" % total)
        print("  modelscope 相关 = %d 条" % len(rows))
        for host, name in rows[:40]:
            print("    %-28s %s" % (host, name))


if __name__ == "__main__":
    main()
