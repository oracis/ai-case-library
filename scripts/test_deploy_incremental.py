# -*- coding: utf-8 -*-
"""deploy_incremental.py 的判据与降级行为（PRD #98）。

实测语义（2026-11-07）：ListObjectsV2 <ETag> == 普通 PUT 对象的 MD5 hex；
multipart 是 "<hex>-N" / Type=Multipart；分页 token 必须走 subresource。
⛔ 本文件最重要的两条回归：
   1. 远端 Size 与本地相同、但 ETag 不同 ⇒ 必须重传（旧 --resume 的坑）；
   2. multipart 形态 / Type=Multipart ⇒ 不许跳过。
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy_incremental as I  # noqa: E402
import deploy_oss as D  # noqa: E402


def _xml(entries, truncated=False, token=None):
    parts = ['<?xml version="1.0" encoding="UTF-8"?><ListBucketResult>']
    for key, etag, typ, size in entries:
        parts.append("<Contents><Key>%s</Key><ETag>%s</ETag><Type>%s</Type><Size>%d</Size></Contents>"
                     % (key, etag, typ, size))
    parts.append("<IsTruncated>%s</IsTruncated>" % ("true" if truncated else "false"))
    if token:
        parts.append("<NextContinuationToken>%s</NextContinuationToken>" % token)
    parts.append("</ListBucketResult>")
    return "".join(parts).encode("utf-8")


def _etag(md5_hex):
    return '"%s"' % md5_hex.upper()


class FakeOSS(object):
    """可编排的 OSS：remote 是 {key: (etag, type, size)}；PUT 记录并返回真实 ETag。"""

    def __init__(self, remote=None, pages=None, list_error=None, put_mode="match"):
        self.remote = dict(remote or {})
        self.pages = pages                  # 显式分页脚本：[xml_bytes, ...]
        self.list_error = list_error        # 若设置，第一次 list 就返回该 (st, body)
        self.put_mode = put_mode
        self.calls = []

    def request(self, method, bucket="", key="", body=None, headers=None,
                subresource="", query=""):
        self.calls.append((method, key, subresource, query, dict(headers or {})))
        if method == "GET" and not key:
            if self.list_error:
                return self.list_error[0], self.list_error[1], {}
            if self.pages:
                page = self.pages.pop(0)
                if isinstance(page, tuple):
                    return page[0], page[1], {}
                return 200, page, {}
            entries = [(k, e, t, s) for k, (e, t, s) in sorted(self.remote.items())]
            return 200, _xml(entries or [("__empty__", _etag("0" * 32), "Normal", 0)]), {}
        if method == "PUT":
            d = hashlib.md5(body or b"").hexdigest()
            if self.put_mode == "match":
                hd = {"ETag": _etag(d)}
            elif self.put_mode == "mismatch":
                hd = {"ETag": _etag("0" * 32)}
            else:
                hd = {}
            self.remote[key] = (hd.get("ETag", ""), "Normal", len(body or b""))
            return 200, b"<ok/>", hd
        return 404, b"", {}


def _make_dir(names, contents=None):
    d = tempfile.mkdtemp()
    contents = contents or {}
    for n in names:
        p = os.path.join(d, n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(contents.get(n, b"x" * 64))
    with open(os.path.join(d, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"inbox": []}, f)
    return d


def _md5_of(d, name):
    with open(os.path.join(d, name), "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _put_keys(oss):
    """被 PUT 的 key 集合。测试按 key 断言，别按数量 —— data.json 也是产物文件。"""
    return {c[1] for c in oss.calls if c[0] == "PUT"}


def _temp_path(d, key):
    return os.path.join(d, key)


def _gate_remote(d):
    """data.json 是 inbox 闸门文件，同时也是真实产物：给它一个内容匹配的远端条目。"""
    m = _md5_of(d, "data.json")
    return {"data.json": (_etag(m), "Normal", os.path.getsize(os.path.join(d, "data.json")))}


class ETagShape(unittest.TestCase):
    def test_norm_etag_strips_quotes_and_case(self):
        self.assertEqual(D.norm_etag(' "ABcd12" '), "abcd12")
        self.assertEqual(D.norm_etag("'ABCD'"), "abcd")

    def test_plain_md5_accepts_32_hex(self):
        self.assertTrue(D.etag_is_plain_md5(_etag("a" * 32)))
        self.assertTrue(D.etag_is_plain_md5("a" * 32))

    def test_multipart_etag_rejected(self):
        self.assertFalse(D.etag_is_plain_md5('"51E0ACFD6F5F61F5AC9F71F5E85BDB4A-1"'))
        self.assertFalse(D.etag_is_plain_md5('"abc"'))
        self.assertFalse(D.etag_is_plain_md5(""))


class CanSkip(unittest.TestCase):
    def setUp(self):
        self.m = hashlib.md5(b"x" * 64).hexdigest()

    def test_matching_normal_etag_skips(self):
        self.assertTrue(I.can_skip({"etag": _etag(self.m), "type": "Normal", "size": 64}, self.m))

    def test_missing_remote_never_skips(self):
        self.assertFalse(I.can_skip(None, self.m))
        self.assertFalse(I.can_skip({}, self.m))

    def test_same_size_but_different_etag_never_skips(self):
        # 核心回归：尺寸一模一样、内容不同 —— 旧 --resume 会在这里误跳过。
        r = {"etag": _etag("0" * 32), "type": "Normal", "size": 64}
        self.assertFalse(I.can_skip(r, self.m))
        r["size"] = 999999
        self.assertFalse(I.can_skip(r, self.m))     # size 改了也不影响结论

    def test_missing_etag_never_skips(self):
        self.assertFalse(I.can_skip({"etag": "", "type": "Normal", "size": 64}, self.m))

    def test_multipart_suffix_never_skips(self):
        r = {"etag": '"%s-1"' % self.m.upper(), "type": "Normal", "size": 64}
        self.assertFalse(I.can_skip(r, self.m))

    def test_multipart_type_never_skips_even_with_plain_etag(self):
        r = {"etag": _etag(self.m), "type": "Multipart", "size": 64}
        self.assertFalse(I.can_skip(r, self.m))

    def test_type_absent_relies_on_etag(self):
        self.assertTrue(I.can_skip({"etag": _etag(self.m), "type": "", "size": 64}, self.m))


class ListObjects(unittest.TestCase):
    def test_single_page(self):
        m = hashlib.md5(b"x").hexdigest()
        oss = FakeOSS(remote={"a.html": (_etag(m), "Normal", 1)})
        out = I.list_objects(oss, "bucket")
        self.assertEqual(set(out), {"a.html"})
        self.assertEqual(out["a.html"]["etag"], _etag(m))

    def test_pagination_token_must_be_subresource(self):
        m1 = hashlib.md5(b"a").hexdigest()
        m2 = hashlib.md5(b"b").hexdigest()
        p1 = _xml([("a.html", _etag(m1), "Normal", 1)], truncated=True, token="TOKEN-X1")
        p2 = _xml([("b.html", _etag(m2), "Normal", 1)])
        oss = FakeOSS(pages=[p1, p2])
        out = I.list_objects(oss, "bucket", page_size=1)
        self.assertEqual(set(out), {"a.html", "b.html"})
        lists = [c for c in oss.calls if c[0] == "GET" and not c[1]]
        self.assertIn("continuation-token=TOKEN-X1", lists[1][2])   # subresource
        self.assertNotIn("continuation-token", lists[1][3])          # 不在 query
        self.assertIn("list-type=2", lists[1][3])

    def test_truncated_without_token_is_incomplete(self):
        p1 = _xml([("a.html", _etag("a" * 32), "Normal", 1)], truncated=True)
        with self.assertRaises(I.IncompleteList):
            I.list_objects(FakeOSS(pages=[p1]), "bucket")

    def test_http_error_is_incomplete(self):
        with self.assertRaises(I.IncompleteList):
            I.list_objects(FakeOSS(list_error=(403, b"<Error/>")), "bucket")

    def test_malformed_xml_is_incomplete(self):
        with self.assertRaises(I.IncompleteList):
            I.list_objects(FakeOSS(pages=[b"not xml"]), "bucket")

    def test_repeated_token_is_incomplete(self):
        p1 = _xml([("a.html", _etag("a" * 32), "Normal", 1)], truncated=True, token="SAME")
        p2 = _xml([("b.html", _etag("b" * 32), "Normal", 1)], truncated=True, token="SAME")
        with self.assertRaises(I.IncompleteList):
            I.list_objects(FakeOSS(pages=[p1, p2, p2]), "bucket")


class SyncPlan(unittest.TestCase):
    def _run(self, remote, dry_run=True, strict=False, verify=False, put_mode="match"):
        d = _make_dir(["a.html", "b.css"])
        ma, mb = _md5_of(d, "a.html"), _md5_of(d, "b.css")
        oss = FakeOSS(remote=remote, put_mode=put_mode)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=dry_run, strict=strict, verify=verify)
        puts = [c for c in oss.calls if c[0] == "PUT"]
        return rc, buf.getvalue(), puts, (ma, mb), d

    def test_matching_objects_are_skipped_without_any_put(self):
        d = _make_dir(["a.html"])
        m = _md5_of(d, "a.html")
        remote = _gate_remote(d)
        remote["a.html"] = (_etag(m), "Normal", 64)
        oss = FakeOSS(remote=remote)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False)
        self.assertEqual(rc, 0)
        self.assertEqual(_put_keys(oss), set())
        self.assertIn("跳过 2", buf.getvalue())
        self.assertIn("需上传 0", buf.getvalue())

    def test_same_size_different_etag_is_reuploaded(self):
        # 远端 size 故意填 64（与本地一致），ETag 却是别的 → 必须 PUT。
        rc, out, puts, (ma, _), d = self._run(
            {"a.html": (_etag("0" * 32), "Normal", 64),
             "b.css": (_etag("0" * 32), "Normal", 64)}, dry_run=False)
        self.assertEqual(rc, 0)
        self.assertEqual({c[1] for c in puts}, {"a.html", "b.css", "data.json"})
        self.assertIn("需上传 3", out)

    def test_multipart_objects_are_reuploaded(self):
        d = _make_dir(["a.html"])
        m = _md5_of(d, "a.html")
        remote = _gate_remote(d)
        remote["a.html"] = ('"%s-1"' % m.upper(), "Multipart", 64)
        oss = FakeOSS(remote=remote)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False)
        self.assertEqual(rc, 0)
        self.assertEqual(_put_keys(oss), {"a.html"})

    def test_put_carries_content_md5(self):
        _rc, _out, puts, _m, _d = self._run({}, dry_run=False)
        self.assertEqual(len(puts), 3)
        for _method, _key, _sub, _q, hd in puts:
            with open(_temp_path(_d, _key), "rb") as f:
                self.assertEqual(base64.b64decode(hd["Content-MD5"]),
                                 hashlib.md5(f.read()).digest())

    def test_bad_etag_response_is_a_hard_failure(self):
        rc, out, _puts, _m, _d = self._run({}, dry_run=False, put_mode="mismatch")
        self.assertNotEqual(rc, 0)
        self.assertIn("BAD-ETAG", out)
        self.assertIn("失败 3", out)

    def test_no_etag_response_is_a_hard_failure(self):
        rc, out, _puts, _m, _d = self._run({}, dry_run=False, put_mode="missing")
        self.assertNotEqual(rc, 0)
        self.assertIn("NO-ETAG", out)

    def test_dry_run_never_writes(self):
        rc, out, puts, _m, _d = self._run({}, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(puts, [])
        self.assertIn("dry-run", out)

    def test_incomplete_list_degrades_to_full_upload(self):
        d = _make_dir(["a.html", "b.css"])
        oss = FakeOSS(list_error=(403, b"<Error/>"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False)
        self.assertEqual(rc, 0)
        self.assertEqual(_put_keys(oss), {"a.html", "b.css", "data.json"})
        self.assertIn("退化为**全量重传**", buf.getvalue())
        self.assertIn("清单降级=全量重传", buf.getvalue())

    def test_strict_incomplete_list_fails_without_any_put(self):
        d = _make_dir(["a.html"])
        oss = FakeOSS(list_error=(403, b"<Error/>"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False, strict=True)
        self.assertNotEqual(rc, 0)
        self.assertEqual([c for c in oss.calls if c[0] == "PUT"], [])

    def test_orphans_are_reported_but_never_deleted(self):
        d = _make_dir(["a.html"])
        m = _md5_of(d, "a.html")
        remote = _gate_remote(d)
        remote["a.html"] = (_etag(m), "Normal", 64)
        remote["old/gone.html"] = (_etag("0" * 32), "Normal", 10)
        oss = FakeOSS(remote=remote)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("old/gone.html", out)
        self.assertIn("仅报告", out)
        self.assertFalse(any(c[0] == "DELETE" for c in oss.calls))

    def test_verify_reads_back_and_detects_drift(self):
        d = _make_dir(["a.html"])
        # 第一次 list 说远端不一致 ⇒ PUT；verify 时第二次 list 仍报旧 ETag ⇒ 硬失败
        stale = (_etag("0" * 32), "Normal", 64)
        gate = _gate_remote(d)
        stale_xml = _xml([("a.html", stale[0], stale[1], stale[2]),
                          ("data.json", gate["data.json"][0], "Normal", gate["data.json"][2])])
        oss = FakeOSS(remote={"a.html": stale})
        oss.pages = [stale_xml, stale_xml]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False, verify=True)
        self.assertNotEqual(rc, 0)
        self.assertIn("回读", buf.getvalue())

    def test_second_run_is_idempotent(self):
        d = _make_dir(["a.html"])
        m = _md5_of(d, "a.html")
        remote = _gate_remote(d)
        remote["a.html"] = (_etag(m), "Normal", 64)
        oss = FakeOSS(remote=remote)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False, verify=True)
        self.assertEqual(rc, 0)
        self.assertEqual(_put_keys(oss), set())
        self.assertIn("2/2 对象 ETag 与本地 MD5 一致", buf.getvalue())

    def test_inbox_gate_blocks_preview_build(self):
        d = _make_dir(["a.html"])
        with open(os.path.join(d, "data.json"), "w", encoding="utf-8") as f:
            json.dump({"inbox": [{"id": "raw"}]}, f)
        oss = FakeOSS()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.sync(oss, "bucket", d, dry_run=False)
        self.assertNotEqual(rc, 0)
        self.assertEqual(oss.calls, [])


class RedLines(unittest.TestCase):
    def test_no_size_based_skip_in_source(self):
        import inspect
        # 只看函数体，别把 docstring 里解释「不看 Size」的文字也算成判据。
        src = inspect.getsource(I.can_skip)
        body = src.split('"""', 2)[-1]
        self.assertNotIn("size", body.lower())
        self.assertNotIn("Content-Length", body)
        self.assertNotIn("HEAD", body)
        self.assertNotIn("[SKIP]", body)

    def test_no_resume_argument(self):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                I.main(["--resume"])
        self.assertEqual(cm.exception.code, 2)
        self.assertNotIn("--resume", buf.getvalue())

    def test_default_is_dry_run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = I.main(["--bucket", "b", "--region", "cn-hongkong", "--dir", "no-such-dir"])
        self.assertNotEqual(rc, 0)

    def test_full_upload_entry_unchanged(self):
        # deploy_oss.upload 仍是无条件全量：远端「同尺寸」也不许发 HEAD 或跳过。
        import inspect
        src = inspect.getsource(D.upload)
        self.assertNotIn("[SKIP]", src)
        self.assertNotIn('"HEAD"', src)
        self.assertNotIn("跳过", src)
        self.assertNotIn("resume", inspect.signature(D.upload).parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
