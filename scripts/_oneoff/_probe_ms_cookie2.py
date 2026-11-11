#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探测2：modelscope cookie 的加密形态 + Local State 里的解密密钥是否可取。

不打印任何密钥明文，只打印前缀与可行性结论。
"""
import base64
import json
import os
import shutil
import sqlite3
import tempfile

LOCAL = os.environ.get("LOCALAPPDATA") or r"C:\Users\DELL\AppData\Local"
PROFILE = os.path.join(LOCAL, "Google", "Chrome", "User Data")
DB = os.path.join(PROFILE, "Default", "Network", "Cookies")
LOCAL_STATE = os.path.join(PROFILE, "Local State")

def main():
    print("[Local State] exists =", os.path.exists(LOCAL_STATE))
    if os.path.exists(LOCAL_STATE):
        try:
            ls = json.load(open(LOCAL_STATE, encoding="utf-8"))
            oc = ls.get("os_crypt", {})
            print("  os_crypt keys =", list(oc.keys()))
            ek = oc.get("encrypted_key")
            print("  encrypted_key present =", bool(ek))
            if ek:
                try:
                    raw = base64.b64decode(ek)
                    print("  encrypted_key prefix  =", raw[:5])
                except Exception as e:
                    print("  b64 失败", e)
            print("  app_bound_encrypted_key present =",
                  bool(oc.get("app_bound_encrypted_key")))
        except Exception as e:
            print("  读取失败:", type(e).__name__, e)

    tmp = os.path.join(tempfile.gettempdir(), "ms_ck_probe2.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    try:
        shutil.copy2(DB, tmp)
    except OSError:
        with open(DB, "rb") as f:
            data = f.read()
        with open(tmp, "wb") as f:
            f.write(data)

    conn = sqlite3.connect(tmp)
    rows = conn.execute(
        "select host_key, name, length(encrypted_value), "
        "substr(cast(encrypted_value as blob),1,3) "
        "from cookies where host_key like '%modelscope%'"
    ).fetchall()
    print("\n[cookies] %d 条" % len(rows))
    for host, name, ln, pre in rows:
        print("  %-24s %-18s len=%-5d prefix=%r" % (host, name, ln, pre))
    conn.close()
    os.remove(tmp)


if __name__ == "__main__":
    main()
