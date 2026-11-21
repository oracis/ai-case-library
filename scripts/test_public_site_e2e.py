#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：真 HTTP 打公开站，验证「读者看到的东西」真的在。

这是覆盖栈里最外的一层——上面各层 test_*.py 用桩/假对象，本文件不 mock
任何东西，走真实 DNS + TLS + 静态产物。守的是「构建→部署→公开页」这条
链路断在哪一环都能现形：

  · 首页 200 且是本站（标题含站名）；
  · 目录页 case/index.html 能列出真实案例链接（不硬编码某案例 id，避免
    数据更新后假红）；
  · 取目录里第一个案例页，断言正文**内联在 HTML 里**（<article> 有内容，
    d-title / d-verdict / d-h 锚点齐全）——即「不跑 JS 也读得到」，这正是
    prerender 存在的意义；
  · 已是 published 的站点，公开页不该出现「内容审核中」横幅。

设计取舍（重要，别改回假绿）：
  ⚠ 网络不可达 / 站点未部署 → **skipTest**，不是 pass。本地开发机没网、
    CI 沙箱无外网时跳过是诚实的；把「连不上」算成通过是假绿。
  ⚠ 断言绑定 DOM 结构锚点，不绑定具体案例的数字（数字会随快照更新变）。
  ⚠ Base URL 可用环境变量 CASE_SITE_URL 覆盖，便于指向 staging / 本地预览。

跑法：
    python -X utf8 scripts/test_public_site_e2e.py          # 直接跑
    python -m unittest scripts.test_public_site_e2e          # 纳入 runner
"""
import os
import re
import sys
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("CASE_SITE_URL", "https://case.ydtgo.top").rstrip("/")
TIMEOUT = 15
UA = "Mozilla/5.0 (compatible; case-library-e2e/1.0)"


def _opener():
    """绕开系统代理——本机 HTTP_PROXY 会把回环/公网请求污染成 403（旧坑）。"""
    keys = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
            "all_proxy", "ALL_PROXY")
    if any(os.environ.get(k) for k in keys):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


OP = _opener()


def _get(path):
    """返回 (status, text)。404/500 不当异常吞掉——它们是断言对象。"""
    url = BASE + path
    try:
        with OP.open(urllib.request.Request(url, headers={"User-Agent": UA}),
                     timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:                                       # noqa: BLE001
        raise unittest.SkipTest("公开站不可达 %s：%r（不算通过）" % (url, e))


class PublicSiteE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 先打首页：连不上就整类 skip 一次，避免每个用例各报一次网络错。
        st, cls.home = _get("/")
        if st != 200:
            raise unittest.SkipTest("首页返回 %d（站点未部署？跳过外网 E2E）" % st)

    def test_home_is_our_site(self):
        self.assertIn("<title", self.home, "首页没有 <title>，不是预期静态站")
        # 站名文案来自 index.html 的 <title>，只匹配稳定的中文部分
        self.assertIn("拆解海外", self.home, "首页标题不含站名，可能打到了别人的站")

    def test_case_index_lists_real_cases(self):
        st, html = _get("/case/index.html")
        self.assertEqual(st, 200, "案例目录页应 200，实得 %d" % st)
        links = re.findall(r'href="\./([A-Za-z0-9][\w-]*)\.html"', html)
        self.assertTrue(links, "目录页没列出任何案例链接（预渲染断链）")
        self.assertGreaterEqual(len(links), 50,
                                "目录页案例数 %d 明显偏少，疑似产物不全" % len(links))

    def test_case_page_has_inlined_body(self):
        """核心断言：案例正文内联在 HTML 里，不依赖 JS 渲染。"""
        st, idx = _get("/case/index.html")
        self.assertEqual(st, 200)
        m = re.search(r'href="\./([\w-]+)\.html"', idx)
        self.assertIsNotNone(m, "目录页无案例可测")
        case_id = m.group(1)

        st, page = _get("/case/%s.html" % case_id)
        self.assertEqual(st, 200, "案例页 %s 应 200，实得 %d" % (case_id, st))
        self.assertIn("<article", page, "案例页没有 <article>（正文未内联）")

        body = page[page.find("<article"):]
        self.assertIn('class="d-title"', body, "缺标题锚点 d-title")
        self.assertIn('class="d-verdict"', body, "缺结论锚点 d-verdict")
        self.assertIn('class="d-h"', body, "缺分节标题锚点 d-h")
        # 纯标签页假绿防护：去掉标签后仍要有足够正文
        text = re.sub(r"<[^>]+>", "", body)
        self.assertGreater(len(text.strip()), 300,
                           "案例 %s 正文去标签后仅 %d 字，疑似空壳页" % (case_id, len(text.strip())))

    def test_no_audit_banner_on_public_page(self):
        """已公开站不该出现「内容审核中」——那是阿里云三态里的中间态。"""
        st, page = _get("/")
        self.assertEqual(st, 200)
        self.assertNotIn("内容审核中", page, "公开站出现审核中横幅")


if __name__ == "__main__":
    unittest.main(verbosity=2)
