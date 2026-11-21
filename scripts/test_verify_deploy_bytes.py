#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立回读脚本的判据单测（纯函数 + mock，不碰网络、不碰线上）。

钉住两件事：
  1. ETag 形态判据 —— multipart 的 "<hex>-N" 必须被拒，否则「半更新」会被
     误判成内容一致（PRD #98）。
  2. verify_one 的三层判据（HTTP / ETag / 字节）各自真能抓到不一致，
     不能只会说「没事」。
"""

import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import verify_deploy_bytes as VB  # noqa: E402


class _Resp:
    def __init__(self, body, etag, status=200):
        self.status = status
        self.headers = {"ETag": etag}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(resp):
    return mock.Mock(open=mock.Mock(return_value=resp))


class TestEtagShape(unittest.TestCase):
    def test_norm_strips_quotes_and_case(self):
        self.assertEqual(VB.norm_etag('  "ABCDEF1234"  '), "abcdef1234")
        self.assertEqual(VB.norm_etag(None), "")

    def test_plain_md5_etag_accepts_32hex(self):
        self.assertTrue(VB.plain_md5_etag('"f8e76434fcbd59954832cfc7cf8fa2c4"'))
        self.assertTrue(VB.plain_md5_etag("F8E76434FCBD59954832CFC7CF8FA2C4"))

    def test_plain_md5_etag_rejects_multipart_and_garbage(self):
        for bad in ('"f8e76434fcbd59954832cfc7cf8fa2c4-2"', "abc", "not-an-etag", ""):
            with self.subTest(bad=bad):
                self.assertFalse(VB.plain_md5_etag(bad))


class TestVerifyOne(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        self.tmp.write(b"<html>hello deploy bytes</html>")
        self.tmp.close()
        self.md5 = hashlib.md5(b"<html>hello deploy bytes</html>").hexdigest()
        self.rel = "case/x.html"

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_all_match_returns_none(self):
        op = _opener(_Resp(b"<html>hello deploy bytes</html>", '"%s"' % self.md5))
        self.assertIsNone(VB.verify_one(op, "https://x.test", self.rel, self.tmp.name))

    def test_etag_mismatch_caught(self):
        op = _opener(_Resp(b"<html>hello deploy bytes</html>", '"%s"' % ("0" * 32)))
        r = VB.verify_one(op, "https://x.test", self.rel, self.tmp.name)
        self.assertIsNotNone(r)
        self.assertIn("ETag 不符", r[1])

    def test_body_bytes_mismatch_caught(self):
        """ETag 对但实收字节不同 —— 语义验收抓不到，这里必须抓到。"""
        op = _opener(_Resp(b"TAMPERED", '"%s"' % self.md5))
        r = VB.verify_one(op, "https://x.test", self.rel, self.tmp.name)
        self.assertIsNotNone(r)
        self.assertIn("字节不符", r[1])

    def test_non_plain_etag_caught(self):
        op = _opener(_Resp(b"<html>hello deploy bytes</html>",
                           '"%s-3"' % self.md5))
        r = VB.verify_one(op, "https://x.test", self.rel, self.tmp.name)
        self.assertIsNotNone(r)
        self.assertIn("非普通 PUT", r[1])

    def test_http_error_caught(self):
        r = VB.verify_one(_opener(_Resp(b"", "", status=404)),
                          "https://x.test", self.rel, self.tmp.name)
        self.assertIsNotNone(r)
        self.assertIn("HTTP 404", r[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
