#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""管理员鉴权（零依赖，只用标准库）。

核实功能是后台功能，只有管理员能操作。这套东西回答三个问题：

1. 凭据存哪：data/admin.json（已 gitignore，绝不入库）。里面是
   密码的 PBKDF2 摘要 + 一个随机 secret，不存明文密码。
2. 密码从哪来：第一次启动自动生成一个随机密码打印在控制台，之后沿用。
   想改就 `python scripts/auth.py --reset`（重新生成）或 `--set 你的密码`，
   也可以用环境变量 CASE_LIB_ADMIN_PASSWORD 覆盖。
3. 登录状态怎么维持：登录后下发一个 HttpOnly cookie，值是 HMAC 签名的
   过期时间戳。好处是服务端不用存 session，重启也不丢登录态；
   代价是改密码后旧 token 依然有效到过期，所以改密码时会换 secret 让旧
   token 全部失效。

用法：
    python scripts/auth.py              # 打印当前状态（不泄露密码）
    python scripts/auth.py --reset      # 重新生成随机密码并打印
    python scripts/auth.py --set abc123 # 把密码设成指定值
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
ADMIN_FILE = os.path.join(DATA_DIR, "admin.json")

# 迭代次数按「2020 年代的单核机器」取，太高会让登录明显卡顿
PBKDF2_ITERATIONS = 200000
# 登录有效期。本站是个人后台，12 小时足够，过期重新输密码
TOKEN_TTL = 12 * 3600
COOKIE_NAME = "case_admin"
ENV_PASSWORD = "CASE_LIB_ADMIN_PASSWORD"

# 去掉 0/O/1/l/I 这些抄写时容易看错的字符
ALPHABET = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_admin():
    if not os.path.exists(ADMIN_FILE):
        return None
    try:
        with open(ADMIN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:                                    # noqa: BLE001
        sys.stderr.write("[auth] data/admin.json 读不了：%s\n" % e)
        return None


def random_password(length=14):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITERATIONS, salt.hex(), dk.hex())


def verify_password(password, stored):
    """校验密码。stored 格式不对（比如手改坏了）一律判失败，不抛异常。"""
    if not stored or not password:
        return False
    parts = str(stored).split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expect = bytes.fromhex(parts[3])
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expect)


def set_password(password, cfg=None):
    """写入新密码。同时换 secret —— 让之前签发的 token 立刻全部失效。"""
    cfg = dict(cfg or {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cfg["password_hash"] = hash_password(password)
    cfg["secret"] = secrets.token_hex(32)
    cfg["updated_at"] = now
    cfg.setdefault("created_at", now)
    _write_json(ADMIN_FILE, cfg)
    return cfg


def ensure_admin(env_password=None):
    """保证凭据存在，返回 (cfg, 明文密码或 None)。

    明文只在「这一轮刚生成」时返回，用来在控制台提示用户；
    沿用已有密码时返回 None（系统自己也不知道原密码是什么）。
    """
    cfg = load_admin()
    if env_password:
        # 环境变量优先，方便临时改密码或自动化场景
        if cfg and verify_password(env_password, cfg.get("password_hash", "")):
            return cfg, None
        return set_password(env_password, cfg), env_password
    if cfg and cfg.get("password_hash"):
        return cfg, None
    pw = random_password()
    return set_password(pw, cfg), pw


def get_secret(cfg):
    secret = (cfg or {}).get("secret")
    if secret:
        return secret
    # 老配置没有 secret（理论上不会），补一个并落盘
    cfg["secret"] = secrets.token_hex(32)
    _write_json(ADMIN_FILE, cfg)
    return cfg["secret"]


def make_token(secret, ttl=TOKEN_TTL):
    """token = 过期时间戳.HMAC签名。无状态，服务端不存 session。"""
    exp = int(time.time()) + int(ttl)
    sig = hmac.new(
        secret.encode("utf-8"), str(exp).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return "%d.%s" % (exp, sig)


def check_token(secret, token):
    """校验 token：格式对、签名对、没过期。任一条不满足都判失败。"""
    if not secret or not token or "." not in str(token):
        return False
    head, _, sig = str(token).rpartition(".")
    try:
        exp = int(head)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    expect = hmac.new(
        secret.encode("utf-8"), str(exp).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expect)


def cookie_header(token, ttl=TOKEN_TTL):
    """HttpOnly：JS 读不到，XSS 偷不走；SameSite=Strict 挡掉跨站带上。"""
    return ("%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
            % (COOKIE_NAME, token, int(ttl)))


def clear_cookie_header():
    return "%s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" % COOKIE_NAME


def main():
    ap = argparse.ArgumentParser(description="管理员凭据管理")
    ap.add_argument("--reset", action="store_true", help="重新生成随机密码")
    ap.add_argument("--set", metavar="PASSWORD", help="把密码设成指定值")
    args = ap.parse_args()

    if args.set:
        set_password(args.set, load_admin())
        print("密码已更新（旧登录态立即失效）。")
        return 0
    if args.reset:
        pw = random_password()
        set_password(pw, load_admin())
        print("新密码：%s" % pw)
        return 0

    cfg = load_admin()
    if not cfg or not cfg.get("password_hash"):
        print("还没设置管理员凭据，下次启动 server.py 会自动生成。")
        return 1
    print("凭据文件：%s" % ADMIN_FILE)
    print("最近更新：%s" % cfg.get("updated_at", "未知"))
    print("（密码以摘要存储，不显示明文。改密码用 --reset 或 --set）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
