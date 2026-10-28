#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知乎 x-zse-96 签名 —— 纯 stdlib 实现（算法逆向常量表，自写移植）。

算法（2023 逆向、2025-2026 多实战帖确认仍用于 web 端）：

    a_v  = "101_3_3.0" + "+" + path[?query] + "+" + d_c0     # /udid 特例：无 "+" 分隔
    sig  = "2.0_" + encrypt(md5(a_v))

encrypt 是知乎自研分组加密（非 AES，尽管流传文章常误称）：
md5 → ord 数组 → 拼 [rand*127, 0] 前缀 + [14]*14 后缀成 48 字节
→ E(16) ^ base_arr ^ 42 → g_r 轮函数得 K(16)
→ F(32) 分两块与 te 异或后过 g_r 得 L(32)
→ M = K+L(48) → 从尾按 3 字节组读，按 i%4 轮换 ^58 位置 → 6bit 查 base_str(64)
→ 64 字符密文。首字节随机 = IV，服务端解密校验 ⇒ **签名每次不同是正常的**。

常量表来源：srx-2000/spider_collection（zhihuEncrypt/zhihu_encrypt.py，2024-04 更新）
与 zhihulite/zhihu_zse96 交叉印证；实现结构为本项目重写（stdlib only）。
⚠ 2026-05-30 后 web 写接口无公开实测成功案例 → 建稿阶段先验证，别直接上 publish。
"""

import ctypes
import hashlib
import json
import random
import urllib.error
import urllib.request

X_ZSE_93 = "101_3_3.0"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

# ---- 常量表（逆向所得，勿改动） --------------------------------------

BASE_STR = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"
BASE_ARR = [48, 53, 57, 48, 53, 51, 102, 55, 100, 49, 53, 101, 48, 49, 100, 55]
H_ZK = [1170614578, 1024848638, 1413669199, -343334464, -766094290, -1373058082,
        -143119608, -297228157, 1933479194, -971186181, -406453910, 460404854,
        -547427574, -1891326262, -1679095901, 2119585428, -2029270069, 2035090028,
        -1521520070, -5587175, -77751101, -2094365853, -1243052806, 1579901135,
        1321810770, 456816404, -1391643889, -229302305, 330002838, -788960546,
        363569021, -1947871109]
H_ZB = [20, 223, 245, 7, 248, 2, 194, 209, 87, 6, 227, 253, 240, 128, 222, 91,
        237, 9, 125, 157, 230, 93, 252, 205, 90, 79, 144, 199, 159, 197, 186,
        167, 39, 37, 156, 198, 38, 42, 43, 168, 217, 153, 15, 103, 80, 189,
        71, 191, 97, 84, 247, 95, 36, 69, 14, 35, 12, 171, 28, 114, 178, 148,
        86, 182, 32, 83, 158, 109, 22, 255, 94, 238, 151, 85, 77, 124, 254, 18,
        4, 26, 123, 176, 232, 193, 131, 172, 143, 142, 150, 30, 10, 146, 162,
        62, 224, 218, 196, 229, 1, 192, 213, 27, 110, 56, 231, 180, 138, 107,
        242, 187, 54, 120, 19, 44, 117, 228, 215, 203, 53, 239, 251, 127, 81,
        11, 133, 96, 204, 132, 41, 115, 73, 55, 249, 147, 102, 48, 122, 145,
        106, 118, 74, 190, 29, 16, 174, 5, 177, 129, 63, 113, 99, 31, 161, 76,
        246, 34, 211, 13, 60, 68, 207, 160, 65, 111, 82, 165, 67, 169, 225,
        57, 112, 244, 155, 51, 236, 200, 233, 58, 61, 47, 100, 137, 185, 64,
        17, 70, 234, 163, 219, 108, 170, 166, 59, 149, 52, 105, 24, 212, 78,
        173, 45, 0, 116, 226, 119, 136, 206, 135, 175, 195, 25, 92, 121, 208,
        126, 139, 3, 75, 141, 21, 130, 98, 241, 40, 154, 66, 184, 49, 181, 46,
        243, 88, 101, 183, 8, 23, 72, 188, 104, 179, 210, 134, 250, 201, 164,
        89, 216, 202, 220, 50, 221, 152, 140, 33, 235, 214]

# ⚠ 此串实为 65 字符（8 个独立实现逐字一致，grep.app 交叉验证）；编码只取 &63，末位死字符勿"修复"
assert len(BASE_STR) == 65 and len(BASE_ARR) == 16 and len(H_ZK) == 32 and len(H_ZB) == 256


# ---- JS 位语义（有符号 32 位） --------------------------------------

def _urs(x, bit):
    """无符号右移（>>>）。"""
    x = ctypes.c_uint32(x).value
    y = bit % 32
    if y == 0:
        return x & 0xFFFFFFFF
    return (x >> y) & 0xFFFFFFFF


def _sls(x, bits):
    """有符号左移（<<，32 位回绕）。"""
    mask = (1 << 32) - 1
    x = x & mask
    r = (x << bits) & mask
    if r >= (1 << 31):
        r -= (1 << 32)
    return r


def _band(x, y):
    return ctypes.c_int(x & y).value


# ---- 分组加密核心 ----------------------------------------------------

def _B_f(tt, te):
    """4 字节拼 32 位大整数。"""
    return (_sls(255 & tt[te], 24) | _sls(255 & tt[te + 1], 16)
            | _sls(255 & tt[te + 2], 8) | 255 & tt[te + 3])


def _i_f(tt, te, tr):
    """32 位大整数拆回 4 字节。"""
    te[tr] = 255 & _urs(tt, 24)
    te[tr + 1] = 255 & _urs(tt, 16)
    te[tr + 2] = 255 & _urs(tt, 8)
    te[tr + 3] = 255 & tt


def _G_f(tt):
    """S-box 轮变换。"""
    te = [0] * 4
    tr = [0] * 4
    _i_f(tt, te, 0)
    tr[0] = H_ZB[255 & te[0]]
    tr[1] = H_ZB[255 & te[1]]
    tr[2] = H_ZB[255 & te[2]]
    tr[3] = H_ZB[255 & te[3]]
    ti = _B_f(tr, 0)
    return ti ^ _Q_f(ti, 2) ^ _Q_f(ti, 10) ^ _Q_f(ti, 18) ^ _Q_f(ti, 24)


def _Q_f(tt, te):
    return _sls(_band(4294967295, tt), te) | _urs(tt, 32 - te)


def _g_r(tt):
    """轮函数：16 字节 → 16 字节。"""
    te = [0] * 16
    tr = [0] * 36
    tr[0] = _B_f(tt, 0)
    tr[1] = _B_f(tt, 4)
    tr[2] = _B_f(tt, 8)
    tr[3] = _B_f(tt, 12)
    for i in range(0, 32):
        ta = _G_f(tr[i + 1] ^ tr[i + 2] ^ tr[i + 3] ^ H_ZK[i])
        tr[i + 4] = tr[i] ^ ta
    _i_f(tr[35], te, 0)
    _i_f(tr[34], te, 4)
    _i_f(tr[33], te, 8)
    _i_f(tr[32], te, 12)
    return te


def _g_x(tt, te):
    """32 字节分两块，各与 te 异或后过 _g_r，te 演化。"""
    tr = []
    for i in range(0, 2):
        tu = tt[i * 16:(i + 1) * 16]
        tc = [None] * 16
        for j in range(0, 16):
            tc[j] = tu[j] ^ te[j]
        te = _g_r(tc)
        tr += te
    return tr


def _final_encrypt(m_v):
    out = ""
    for i in range(len(m_v) - 1, -1, -3):
        if i % 4 == 3:
            a_v = (m_v[i] ^ 58) + _sls(m_v[i - 1], 8) + _sls(m_v[i - 2], 16)
        elif i % 4 == 0:
            a_v = m_v[i] + _sls(m_v[i - 1] ^ 58, 8) + _sls(m_v[i - 2], 16)
        elif i % 4 == 1:
            a_v = m_v[i] + _sls(m_v[i - 1], 8) + _sls(m_v[i - 2] ^ 58, 16)
        else:
            a_v = m_v[i] + _sls(m_v[i - 1], 8) + _sls(m_v[i - 2], 16)
        out += BASE_STR[a_v & 63]
        out += BASE_STR[_urs(a_v, 6) & 63]
        out += BASE_STR[_urs(a_v, 12) & 63]
        out += BASE_STR[_urs(a_v, 18)]
    return out


def _encode(a_v, rng=random):
    """md5 → 48 字节数组 → 分组加密 → 64 字符密文。"""
    b_v = hashlib.md5(a_v.encode()).hexdigest()
    c_v = [ord(i) for i in b_v]
    d_v = [int(127 * rng.random()), 0] + c_v + [14] * 14
    e_v, f_v = d_v[0:16], d_v[16:48]
    j_v = [e_v[i] ^ BASE_ARR[i] ^ 42 for i in range(len(e_v))]
    k_v = _g_r(j_v)
    l_v = _g_x(f_v, k_v)
    return _final_encrypt(k_v + l_v)


def sign(path_query, d_c0, rng=random):
    """生成 x-zse-96。path_query 形如 '/api/v4/articles'（含 query 时带 '?...'）。"""
    a_v = "%s+%s+%s" % (X_ZSE_93, path_query, d_c0)
    return "2.0_" + _encode(a_v, rng)


def _opener():
    # 国内站直连，不吃系统代理
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_d_c0():
    """匿名领 d_c0：带签名 POST /udid（此签名不含 d_c0，特例 '101_3_3.0/udid'）。"""
    headers = {
        "User-Agent": UA,
        "x-zse-93": X_ZSE_93,
        "x-zse-96": "2.0_" + _encode(X_ZSE_93 + "/udid"),
        "accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.zhihu.com",
        "Referer": "https://www.zhihu.com/",
    }
    req = urllib.request.Request("https://www.zhihu.com/udid", data=b"", method="POST")
    for k, v in headers.items():
        req.add_header(k, v)

    class _Jar:
        pass
    op = _opener()
    with op.open(req, timeout=20) as r:
        set_cookies = r.headers.get_all("Set-Cookie") or []
    for sc in set_cookies:
        for kv in sc.split(";"):
            kv = kv.strip()
            if kv.startswith("d_c0="):
                return kv[5:].split(";")[0]
    raise RuntimeError("zhihu /udid 未回 d_c0（可能算法/前端版本变了）")


def api(path_query, d_c0, cookie="", method="GET", data=None, xsrf="", timeout=20):
    """带签名调 zhihu api。返回 (http_status, parsed_dict|str)。"""
    headers = {
        "User-Agent": UA,
        "x-zse-93": X_ZSE_93,
        "x-zse-96": sign(path_query, d_c0),
        "accept": "*/*",
        "Referer": "https://www.zhihu.com/",
        "Cookie": cookie or ("d_c0=%s;" % d_c0),
    }
    if method == "POST":
        headers["Content-Type"] = "application/json"
        headers["Origin"] = "https://www.zhihu.com"
        if xsrf:
            headers["x-xsrftoken"] = xsrf
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    req = urllib.request.Request("https://www.zhihu.com" + path_query,
                                 data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with _opener().open(req, timeout=timeout) as r:
            raw, status = r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw[:400]


if __name__ == "__main__":
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8", errors="replace")
    d = get_d_c0()
    print("d_c0 领取成功:", d[:20], "...")
    st, body = api("/api/v4/members/laomi-lan/followers"
                   "?include=data%5B*%5D.answer_count&offset=0&limit=1", d)
    print("只读接口验证 -> HTTP", st, "|", json.dumps(body, ensure_ascii=False)[:200]
          if isinstance(body, dict) else str(body)[:200])
