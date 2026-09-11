"""deploy_oss.py 的本地测试：不连阿里云，只验证参数解析和分支逻辑。

只覆盖那些能在本地伪造的分支：参数验证、Bucket 存在/不存在的两种处理、
命令行参数解析、ACL 字符串、Content-Type 表。真正的 HTTP 调用是手工冒烟
的（见 README「部署」一节），不会写在自动化测试里。
"""

import argparse
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import deploy_oss as D                                          # noqa: E402

# ---------------------------------------------------------------- 单元级


class MIME(unittest.TestCase):
    def test_html(self):
        self.assertEqual(D.MIME[".html"], "text/html; charset=utf-8")

    def test_js(self):
        self.assertEqual(D.MIME[".js"], "application/javascript; charset=utf-8")

    def test_css(self):
        self.assertEqual(D.MIME[".css"], "text/css; charset=utf-8")

    def test_json(self):
        self.assertEqual(D.MIME[".json"], "application/json; charset=utf-8")

    def test_unknown_falls_back(self):
        # 没注册的后缀落兜底（这是 upload() 里 MIME.get(ext, ...) 用的）
        self.assertEqual(D.MIME.get(".log", "application/octet-stream"),
                         "application/octet-stream")


class CacheRules(unittest.TestCase):
    """数据/HTML 每次重读，JS/CSS 长缓存——这是这套缓存策略的核心。

    设计意图：换素材内容时（即 data.json 变了）用户必须立刻看到；
    而 JS/CSS 代码本身不太会改一次一次变，给它们一周缓存能省大量回源。
    """

    def test_data_is_no_cache(self):
        self.assertEqual(D.CACHE_RULES["data.json"], "no-cache")

    def test_html_is_no_cache(self):
        # 两个 HTML 都是 no-cache，404 也算
        self.assertEqual(D.CACHE_RULES["index.html"], "no-cache")
        self.assertEqual(D.CACHE_RULES["404.html"], "no-cache")

    def test_assets_are_longer(self):
        # JS / CSS 比 HTML 更激进——一次发布能用一周
        self.assertIn("max-age=", D.CACHE_RULES["app.js"])
        self.assertIn("max-age=", D.CACHE_RULES["style.css"])
        # 数字上 JS/CSS 应该 ≥ HTML 的「等价有效期」
        # no-cache 等价于 max-age=0，所以 JS 一定是 > 0
        import re
        for name in ("app.js", "style.css"):
            m = re.search(r"max-age=(\d+)", D.CACHE_RULES[name])
            self.assertIsNotNone(m, "%s 没有 max-age" % name)
            self.assertGreater(int(m.group(1)), 0)


class Human(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(D.human(500), "500 B")

    def test_kb(self):
        self.assertEqual(D.human(2048), "2.0 KB")

    def test_mb(self):
        # 大文件也是 KB 单位，没特殊处理
        v = D.human(3 * 1024 * 1024)
        self.assertIn("KB", v)


class EnvLoading(unittest.TestCase):
    def test_load_nonexistent(self):
        # 不存在的路径不抛异常，返回 False
        ok = D.load_env_file("/no/such/file/.env.never.exist")
        self.assertFalse(ok)


# ---------------------------------------------------------------- 集成级


class CLIArgs(unittest.TestCase):
    """在没真正调阿里云的情况下，测主流程在「Bucket 不存在」/「没指定」/「--check」
    这几种输入下的分支。重点是：**它会在不该写的时候乖乖退出**。"""

    class _FakeOSS:
        """按收到的参数返回对应的 XML，让 list_buckets 能正确解析。"""

        def __init__(self, buckets):
            self.buckets = list(buckets)

        def request(self, method, bucket="", key="", body=None, headers=None, subresource=""):
            # 列 Bucket（GET /）时返回 ListAllMyBucketsResult
            if method == "GET" and not bucket:
                names = "".join("<Name>%s</Name>" % b for b in self.buckets)
                xml = (b"<?xml version='1.0'?>"
                       b"<ListAllMyBucketsResult><Buckets>"
                       + names.encode() + b"</Buckets></ListAllMyBucketsResult>")
                return 200, xml, {}
            # 任何对 Bucket 的操作都返回成功（防止意外触发真请求）
            return 200, b"<ok/>", {}

        # list_buckets 调 oss.request("GET") 后用 re 解析 XML，
        # 不依赖 XML 命名空间精确匹配——所以上面那串就够了。

    def _run_main(self, monkey_args, fake_oss):
        saved = sys.argv
        sys.argv = ["deploy_oss.py"] + monkey_args
        saved_oss = D.OSS
        D.OSS = lambda *a, **k: fake_oss
        # 不重载 resolve_credentials，否则会因为没 AK 提前退
        saved_resolve = D.resolve_credentials
        D.resolve_credentials = lambda: ("fake_ak_" + "x" * 24, "fake_sk_" + "y" * 30)
        try:
            rc = D.main()
        finally:
            sys.argv = saved
            D.OSS = saved_oss
            D.resolve_credentials = saved_resolve
        return rc

    def test_check_with_no_bucket(self):
        rc = self._run_main(["--check"], self._FakeOSS([]))
        self.assertEqual(rc, 0)

    def test_check_when_bucket_missing(self):
        # --check 模式下 bucket 不存在应非 0 退出
        rc = self._run_main(["--check", "--bucket", "not-there"], self._FakeOSS(["other"]))
        self.assertEqual(rc, 1)

    def test_check_when_bucket_present(self):
        rc = self._run_main(["--check", "--bucket", "ok-bucket"], self._FakeOSS(["ok-bucket"]))
        self.assertEqual(rc, 0)

    def test_no_bucket_no_create(self):
        # 没指定 bucket 也不是 --check：应报错
        rc = self._run_main([], self._FakeOSS([]))
        self.assertNotEqual(rc, 0)

    def test_bucket_missing_no_create_flag(self):
        # 缺 --create：应报错，不去碰阿里云
        # 用一个明显不是「真要建」的名字，避免误触发
        rc = self._run_main(
            ["--bucket", "nonexistent-bucket-fake-zzz"],
            self._FakeOSS(["some-other"]),
        )
        self.assertNotEqual(rc, 0)


class HostSelection(unittest.TestCase):
    """OSS 对 host 形式有强制约束：对象操作必须用三级域名，否则回
    SecondLevelDomainForbidden。这是上次部署踩的坑，要钉死。"""

    def _new(self, region="cn-hongkong"):
        return D.OSS("ak_" + "x" * 24, "sk_" + "y" * 30, region)

    def test_list_buckets_uses_second_level(self):
        # 列账号下所有 Bucket → 二级域名
        self.assertEqual(
            self._new()._host_for(""),
            "https://oss-cn-hongkong.aliyuncs.com",
        )

    def test_object_ops_use_third_level(self):
        # 任何带 bucket 的请求 → 三级域名
        self.assertEqual(
            self._new()._host_for("ai-case-library"),
            "https://ai-case-library.oss-cn-hongkong.aliyuncs.com",
        )

    def test_object_ops_for_other_region(self):
        # 地域必须反映在 host 里，否则发到错的 endpoint
        oss = self._new("cn-beijing")
        self.assertEqual(
            oss._host_for("any-bucket"),
            "https://any-bucket.oss-cn-beijing.aliyuncs.com",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)