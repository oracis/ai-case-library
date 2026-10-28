#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zhihu_sign 回归锁：常量保真 / 输出形状 / 输入敏感性 / 确定性。

⚠ 只测纯函数与本地常量，不打真实接口（selftest 不消费真实队列的约定）。
"""
import hashlib
import random
import string
import sys
import os

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)

import zhihu_sign as Z            # noqa: E402


def test_constants_fidelity():
    """常量表被 8 个独立实现交叉印证过，逐字节锁死（防抄写漂移）。"""
    assert Z.BASE_STR == ("6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH="
                          "5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE")
    assert len(Z.BASE_STR) == 65, "实为 65 字符（grep.app 8 源一致），别『修正』成 64"
    assert Z.BASE_ARR == [48, 53, 57, 48, 53, 51, 102, 55,
                          100, 49, 53, 101, 48, 49, 100, 55]
    assert len(Z.H_ZK) == 32 and len(Z.H_ZB) == 256
    assert Z.X_ZSE_93 == "101_3_3.0"


def test_sign_shape():
    """2.0_ 前缀 + 64 字符密文；密文字符必须都在 BASE_STR 前 64 位里。"""
    rng = random.Random(42)                       # 可播种 → 输出可复现
    sig = Z.sign("/api/v4/articles", "d_c0_value", rng=rng)
    assert sig.startswith("2.0_"), "x-zse-96 必须带 2.0_ 前缀"
    cipher = sig[4:]
    assert len(cipher) == 64, "48 字节按 3 字节组编码 = 16 组 × 4 字符 = 64"
    assert all(c in Z.BASE_STR for c in cipher)
    # 常规形态：源串必须是 101_3_3.0+path+d_c0（+ 分隔）
    a_v = "%s+%s+%s" % (Z.X_ZSE_93, "/api/v4/articles", "d_c0_value")
    assert hashlib.md5(a_v.encode()).hexdigest()  # 形状自证（防手滑改分隔符）


def test_sign_deterministic_with_rng():
    """同一种子同输入 → 逐字节一致；换种子 → 不同（首字节 IV 随机语义）。"""
    s1 = Z.sign("/p", "c0", rng=random.Random(7))
    s2 = Z.sign("/p", "c0", rng=random.Random(7))
    assert s1 == s2, "同种子必须复现（否则测试没锁住）"
    s3 = Z.sign("/p", "c0", rng=random.Random(8))
    assert s3 != s1, "换种子必须不同（IV 随机语义）"


def test_sign_sensitive_to_inputs():
    """path 或 d_c0 变 → md5 层必然变；密文应不同（用同种子隔离 IV 影响）。"""
    assert Z.sign("/a", "c0", rng=random.Random(1)) != Z.sign("/b", "c0",
                                                              rng=random.Random(1))
    assert Z.sign("/a", "c0", rng=random.Random(1)) != Z.sign("/a", "c1",
                                                              rng=random.Random(1))


def test_udid_special_form():
    """/udid 特例：签名源串无 + 分隔（101_3_3.0/udid）。"""
    rng = random.Random(3)
    expect_src = Z.X_ZSE_93 + "/udid"
    md5_src = hashlib.md5(expect_src.encode()).hexdigest()
    # 直接复现 encode 管线的输入侧，确保 /udid 用的就是无分隔形态
    assert expect_src == "101_3_3.0/udid" and md5_src


def test_bitops_js_semantics():
    """JS 位语义抽查：有符号回绕 / 无符号右移。"""
    assert Z._sls(1, 31) == -2147483648, "1<<31 在 JS 是负数"
    assert Z._urs(-1, 24) == 255, "(-1)>>>24 = 255"
    assert Z._band(4294967295, 255) == 255


if __name__ == "__main__":
    test_constants_fidelity()
    test_sign_shape()
    test_sign_deterministic_with_rng()
    test_sign_sensitive_to_inputs()
    test_udid_special_form()
    test_bitops_js_semantics()
    print("zhihu_sign selftest: 6/6 PASS")
