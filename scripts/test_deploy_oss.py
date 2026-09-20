"""deploy_oss.py 的本地测试：不连阿里云，只验证参数解析和分支逻辑。

只覆盖那些能在本地伪造的分支：参数验证、Bucket 存在/不存在的两种处理、
命令行参数解析、ACL 字符串、Content-Type 表。真正的 HTTP 调用是手工冒烟
的（见 README「部署」一节），不会写在自动化测试里。
"""

import argparse
import json
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

    def _run_main(self, monkey_args, fake_oss, region_env=None):
        """跑一次 main()，返回 (rc, 有没有真的构造过 OSS 客户端)。

        第二个返回值用来钉「闸门拦在前面，根本没碰阿里云」：
        以前只看 rc，一个「先构造 OSS 再校验参数」的实现也能骗过测试。
        """
        saved = sys.argv
        sys.argv = ["deploy_oss.py"] + monkey_args
        saved_oss = D.OSS
        touched = []

        def _spy(*a, **k):
            touched.append(a)
            return fake_oss

        D.OSS = _spy
        # 不重载 resolve_credentials，否则会因为没 AK 提前退
        saved_resolve = D.resolve_credentials
        D.resolve_credentials = lambda: ("fake_ak_" + "x" * 24, "fake_sk_" + "y" * 30)
        saved_env = os.environ.get("OSS_REGION")
        # 环境里可能真的设了 OSS_REGION，会让「不传地域」的用例意外通过，
        # 所以显式清掉，再由 region_env 决定要不要放回来。
        os.environ.pop("OSS_REGION", None)
        if region_env:
            os.environ["OSS_REGION"] = region_env
        try:
            rc = D.main()
        finally:
            sys.argv = saved
            D.OSS = saved_oss
            D.resolve_credentials = saved_resolve
            if saved_env is None:
                os.environ.pop("OSS_REGION", None)
            else:
                os.environ["OSS_REGION"] = saved_env
        return rc, bool(touched)

    # ---- 地域：必填，没有兜底 ----

    def test_region_is_required(self):
        """不给地域就该当场退出，而且**在构造 OSS 客户端之前**退出。

        曾经的兜底是 cn-hangzhou，本项目 Bucket 却在 cn-hongkong，
        于是「什么都不填」必然指向错 endpoint，OSS 回一句
        "must be addressed using the specified endpoint" 403 ——
        看不出是地域错了。宁可停下来说清楚要什么。
        """
        rc, touched = self._run_main(["--check", "--bucket", "ok-bucket"],
                                     self._FakeOSS(["ok-bucket"]))
        self.assertEqual(rc, 1)
        self.assertFalse(touched, "没给地域就构造了 OSS 客户端，说明闸门在部署动作之后")

    def test_no_hangzhou_fallback(self):
        """钉住「不拿 cn-hangzhou 当默认值」。

        这条是本次改动的核心：只要有人把兜底加回来，就会失败。
        """
        src = open(os.path.join(os.path.dirname(os.path.abspath(D.__file__)),
                                "deploy_oss.py"), encoding="utf-8").read()
        self.assertNotIn('"cn-hangzhou"', src)
        self.assertNotIn("'cn-hangzhou'", src)

    def test_region_via_env_var(self):
        """环境变量 OSS_REGION 是合法来源（CI 与 --env-file 都靠它）。"""
        rc, touched = self._run_main(["--check", "--bucket", "ok-bucket"],
                                     self._FakeOSS(["ok-bucket"]),
                                     region_env="cn-hongkong")
        self.assertEqual(rc, 0)
        self.assertTrue(touched)

    def test_region_arg_wins_over_env(self):
        """命令行优先于环境变量 —— 否则没法临时换 bucket。"""
        rc, touched = self._run_main(
            ["--check", "--bucket", "ok-bucket", "--region", "cn-beijing"],
            self._FakeOSS(["ok-bucket"]), region_env="cn-hongkong")
        self.assertEqual(rc, 0)
        self.assertTrue(touched)

    def test_check_with_no_bucket(self):
        rc, _ = self._run_main(["--check", "--region", "cn-hongkong"],
                               self._FakeOSS([]))
        self.assertEqual(rc, 0)

    def test_check_when_bucket_missing(self):
        # --check 模式下 bucket 不存在应非 0 退出
        rc, _ = self._run_main(["--check", "--region", "cn-hongkong",
                                "--bucket", "not-there"], self._FakeOSS(["other"]))
        self.assertEqual(rc, 1)

    def test_check_when_bucket_present(self):
        rc, _ = self._run_main(["--check", "--region", "cn-hongkong",
                                "--bucket", "ok-bucket"], self._FakeOSS(["ok-bucket"]))
        self.assertEqual(rc, 0)

    def test_no_bucket_no_create(self):
        # 没指定 bucket 也不是 --check：应报错
        rc, _ = self._run_main(["--region", "cn-hongkong"], self._FakeOSS([]))
        self.assertNotEqual(rc, 0)

    def test_bucket_missing_no_create_flag(self):
        # 缺 --create：应报错，不去碰阿里云
        # 用一个明显不是「真要建」的名字，避免误触发
        rc, _ = self._run_main(
            ["--region", "cn-hongkong", "--bucket", "nonexistent-bucket-fake-zzz"],
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


class InboxGate(unittest.TestCase):
    """对外发布不许带未核实队列。

    2026-09-20 实际踩过：`--dir` 默认是 dist，而 dist 是带 inbox 的本地预览版，
    于是 441 条未核实素材被推上了公网，直到线上语义校验报 `inbox = 0` 才暴露。
    这个错误的代价不对称 —— 它不报错，只是静静被收录。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, payload):
        path = os.path.join(self.tmp, "data.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def test_counts_inbox(self):
        path = self._write({"cases": [1, 2], "inbox": [1, 2, 3, 4]})
        self.assertEqual(D.inbox_count(path), 4)

    def test_clean_build_returns_zero(self):
        path = self._write({"cases": [1, 2], "inbox": []})
        self.assertEqual(D.inbox_count(path), 0)

    def test_missing_file_returns_none(self):
        """读不出来必须和「确实是 0」区分开。

        第一版把两者都返回 0，结果漏掉了 `import json` 这个真 bug ——
        异常被当成「干净」，闸门一直开着却看起来一切正常。
        """
        self.assertIsNone(D.inbox_count(os.path.join(self.tmp, "nope.json")))

    def test_malformed_json_returns_none(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{不是合法 JSON")
        self.assertIsNone(D.inbox_count(path))

    def test_dict_without_inbox_key_returns_none(self):
        """缺 inbox 字段说明产物结构不对，不能当成「没有未核实素材」。"""
        path = self._write({"cases": [1, 2]})
        self.assertIsNone(D.inbox_count(path))

    def test_real_builds(self):
        """真实产物：dist 带队列、public 不带 —— 这就是那道闸门的分界线。"""
        dist = os.path.join(ROOT, "dist", "data.json")
        public = os.path.join(ROOT, "public", "data.json")
        if not os.path.exists(dist) or not os.path.exists(public):
            self.skipTest("还没构建产物")
        self.assertGreater(D.inbox_count(dist), 0, "dist 应当带未核实队列")
        self.assertEqual(D.inbox_count(public), 0, "public 必须不含未核实队列")


if __name__ == "__main__":
    unittest.main(verbosity=2)