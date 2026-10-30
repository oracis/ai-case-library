#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auth.get_secret 的回归测试。

守的是 2026-10-29 那次事故：一次「读密钥」的调用把凭据换了。
调用方传空配置（server 的 `_ADMIN_CFG` 常年是 `{}`，只在登录后才填全）时，
旧实现会现场生成一个新 secret 并**整文件覆盖** data/admin.json ——
所有已登录会话失效，password_hash 被一起抹掉（301 字节 → 82 字节）。

这个错误的险恶之处在于：调用方只是想读一个值，函数却顺手改了全局状态，
而且写盘前后没有任何提示。所以这里锁死「读就是读」这条边界。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import auth                                                   # noqa: E402


class GetSecret(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="authtest_")
        self.path = os.path.join(self.dir, "admin.json")
        self.orig = auth.ADMIN_FILE
        auth.ADMIN_FILE = self.path
        self.cfg = {
            "password_hash": "pbkdf2_sha256$200000$abcdef$0123456789",
            "secret": "s" * 64,
            "updated_at": "2026-09-30 11:44:13",
            "created_at": "2026-09-15 23:10:00",
        }
        self._write(self.cfg)

    def tearDown(self):
        auth.ADMIN_FILE = self.orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, payload):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_传空配置时回磁盘读_不轮换secret(self):
        """server 的 _ADMIN_CFG={} 是常态：读密钥绝不能顺手换掉它。"""
        got = auth.get_secret({})
        self.assertEqual(got, "s" * 64)
        self.assertEqual(self._read(), self.cfg, "文件一个字节都不该被改写")

    def test_传None也不轮换(self):
        self.assertEqual(auth.get_secret(None), "s" * 64)
        self.assertEqual(self._read(), self.cfg)

    def test_传完整配置时直接用不读盘(self):
        self.assertEqual(auth.get_secret({"secret": "x" * 64}), "x" * 64)

    def test_文件缺secret时才补_且保留password_hash(self):
        """真要补也只是补那一个键 —— 不能把密码哈希一起抹掉。"""
        broken = dict(self.cfg)
        broken.pop("secret")
        self._write(broken)
        got = auth.get_secret({})
        after = self._read()
        self.assertTrue(got and got != "s" * 64, "应该补出一个新的非空 secret")
        self.assertEqual(after["password_hash"], self.cfg["password_hash"],
                         "password_hash 必须原样留着")
        self.assertEqual(after["updated_at"], self.cfg["updated_at"])
        self.assertEqual(after["secret"], got)

    def test_补完后再次读取是稳定的(self):
        """补过一次之后不能每次调用都换一个（否则会话永远活不过一次调用）。"""
        broken = dict(self.cfg)
        broken.pop("secret")
        self._write(broken)
        first = auth.get_secret({})
        second = auth.get_secret({})
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
