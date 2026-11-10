#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布守卫回归锁：幂等 / 标题 / 内容形态 / 封面 / 唯一约束 / 生成器。

全部离线：不联网、不碰浏览器、不写真台账（SQLite 用 LEDGER_DB 指到 tmp，
JSON 台账用临时文件），跑前跑后 `data/ledger.db` 零变化。

跑法：cd scripts && python -X utf8 -m unittest test_publish_guard -v
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import db                        # noqa: E402
import gen_platform_md as GM     # noqa: E402
import publish_guard as G        # noqa: E402
import publish_one as P          # noqa: E402
import publish_slot as S         # noqa: E402


# ---------------------------------------------------------------- 口径一致性

class Caliber(unittest.TestCase):
    def test_norm_matches_slot(self):
        for cid in ("ext-ep-001-x", "ep-001-x", "chessigma", "", None):
            self.assertEqual(G.norm_id(cid), S._norm_id(cid))

    def test_title_max_matches_audit_tool(self):
        # 前四个是既有实测口径，改动必须两边同步
        for k, v in (("toutiao", 30), ("xhs", 20), ("wechat", 64), ("bilibili", 80)):
            self.assertEqual(G.TITLE_MAX[k], v)

    def test_all_nine_platforms_in_tables(self):
        keys = {k for k, _ in S.PLATFORMS}
        for table in (G.TITLE_MAX, G.COVER_REQUIRED):
            self.assertTrue(keys <= set(table), "平台表有洞：%s" % (keys - set(table)))
        for key in keys:
            for gate in ("dedupe", "draft", "link", "title", "template", "cover"):
                self.assertIn(gate, P.GATES[key], "%s 缺 %s 闸" % (key, gate))


# ---------------------------------------------------------------- 标题

class TitleScan(unittest.TestCase):
    def rules(self, plat, title):
        return {f["rule"] for f in G.scan_title(plat, title)}

    def test_clean_title_passes(self):
        t = "盯住 5 万个招聘页：招聘聚合系统的采集调度与去重"
        self.assertEqual([f for f in G.scan_title("cnblogs", t)
                          if f["level"] == "block"], [])

    def test_title_dash_blocked(self):
        t = "把 AI 建站工具生成的网站，转成 WordPress——一个工程师的生意"
        fs = G.scan_title("wechat", t)
        self.assertIn("标题含破折号", {f["rule"] for f in fs})
        self.assertTrue(any(f["level"] == "block" for f in fs))
        # 单个 ASCII 连字符是正当写法，不许误伤
        self.assertNotIn("标题含破折号",
                         self.rules("cnblogs", "AI-native 建站工具的导出链路"))
        # 已发表产物降 warn，别把全仓体检锁死
        self.assertFalse(any(f["level"] == "block"
                             for f in G.scan_title("wechat", t, published=True)))

    def test_over_limit_blocked(self):
        t = "用麦当劳官方接口查了一下，我的积分正在悄悄过期" * 2
        self.assertIn("标题超平台上限", self.rules("toutiao", t))

    def test_hard_cut_detected(self):
        # 长度恰好 30 且尾字是虚字 = t[:30] 的形状
        t = ("一个把招聘页聚合起来做打分的小工具" * 3)[:29] + "的"
        self.assertEqual(len(t), 30)
        self.assertIn("疑似硬截断", self.rules("toutiao", t))

    def test_empty_template_blocked(self):
        for t in ("一个海外小产品怎么赚钱：Chessigma",
                  "云原生视角下的独立产品增长：Codédex",
                  "Chessigma 技术拆解：一个海外独立产品的工程与增长"):
            self.assertIn("空壳/机械模板标题", self.rules("zhihu", t), t)

    def test_ai_filler_is_warn(self):
        fs = G.scan_title("cnblogs", "一个轻松打造的一站式工具：把事情做完")
        self.assertTrue(any(f["rule"] == "AI 味套话" for f in fs))
        self.assertFalse(any(f["level"] == "block" and f["rule"] == "AI 味套话"
                             for f in fs))

    def test_katex_platform_forbids_dollar(self):
        for p in G.KATEX_PLATFORMS:
            self.assertIn("标题含 `$`", self.rules(p, "上线 5 天做到 $18,380 MRR 的产品"))
        self.assertNotIn("标题含 `$`", self.rules("cnblogs", "上线 5 天做到 $18,380 MRR 的产品"))

    def test_all_platforms_forbid_money(self):
        """2026-11-04：禁金额从「技术平台」扩到**所有平台**。

        用户口径：标题是产品的简要介绍，不是收入播报；金额是不准确的
        动态快照，写进标题必然过期（封面第二行也不显示金额）。
        """
        for p in G.TITLE_MAX:
            self.assertIn("标题带金额", self.rules(p, "月入 4 万美元的一个小工具"), p)

    def test_money_variants(self):
        for t in ("1Lookup：MRR $244,029", "近 30 天收入 $16,120",
                  "自助 ¥329/月起", "solo 自举做到几百万美金", "百万年薪的机会"):
            self.assertIn("标题带金额", self.rules("wechat", t), t)

    def test_published_money_is_warn_only(self):
        """已发布产物不可改（会导致已发表列表认亲错位）⇒ 只提示不判红。"""
        f = [x for x in G.scan_title("wechat", "1Lookup：MRR $244,029", published=True)
             if x["rule"] == "标题带金额"]
        self.assertTrue(f)
        self.assertEqual(f[0]["level"], "warn")

    def test_scale_numbers_are_not_money(self):
        # 「100 万用户」「5 万个招聘页」「576 人付费」是可核验规模事实
        for t in ("100 万用户里只有 576 人付费", "盯住 5 万个招聘页：招聘聚合系统的采集调度",
                  "AI 生成跑步计划，为跑者信心定价"):
            self.assertNotIn("标题带金额", self.rules("wechat", t), t)

    def test_title_matches_artifact_first_line(self):
        fd, path = tempfile.mkstemp(suffix=".md", text=True)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 实际产物标题\n\n正文\n")
        try:
            fs = G.scan_title("cnblogs", "台账里的旧标题", path)
            self.assertIn("标题与产物首行不一致", {x["rule"] for x in fs})
        finally:
            os.remove(path)


# ---------------------------------------------------------------- 内容形态

class ContentScan(unittest.TestCase):
    WATER = "# 标题\n\n> 平台：zhihu ｜ 案例：x（x）｜ 状态：本地稿，未推送\n\n正文\n"
    CANNED = "# 标题\n\n来源：官网 + TrustMRR 支付验证页 + 公开访谈。\n"
    MECH = ("# 标题\n\n## 一、它到底解决了什么\n\n## 二、钱从哪来\n\n"
            "## 三、为什么它能成\n\n## 四、能抄的做法\n\n正文\n")

    def rules(self, plat, text, level=None):
        return {f["rule"] for f in G.scan_content(plat, text)
                if level is None or f["level"] == level}

    def test_watermark_blocked_on_tech_platforms(self):
        for p in G.TECH_PLATFORMS:
            self.assertIn("管线水印", self.rules(p, self.WATER, "block"))

    def test_watermark_only_warn_on_social(self):
        for p in ("wechat", "toutiao", "xhs", "bilibili"):
            self.assertIn("管线水印", self.rules(p, self.WATER, "warn"))
            self.assertNotIn("管线水印", self.rules(p, self.WATER, "block"))

    def test_canned_source_blocked_on_tech(self):
        self.assertIn("罐头来源句", self.rules("csdn", self.CANNED, "block"))

    def test_mechanical_sections_blocked_on_tech(self):
        self.assertIn("机械栏目骨架", self.rules("csdn", self.MECH, "block"))

    def test_zhihu_cta_blocked(self):
        text = "# 标题\n\n想看完整版去公众号，扫码加群领资料。\n"
        self.assertIn("知乎导流/联系方式", self.rules("zhihu", text, "block"))

    def test_clean_body_passes(self):
        text = "# 标题\n\n## 问题边界\n\n具体的工程取舍……\n\n## 适用前提\n\n……\n"
        self.assertEqual([f for f in G.scan_content("tencentcloud", text)
                          if f["level"] == "block"], [])

    # ---- 内部核验注记（PRD #124）------------------------------------
    # 11-10 B站一号 FIFO 发出 10-20 积压旧稿，正文开头带这段内部核查说明。
    VERIFY_MD = ("# 标题\n\n> 我去核了一遍：所给原文中不存在任何 TrustMRR "
                 "成交记录，该数字目前无任何来源。\n\n## 它是干什么的\n\n正文。\n")
    VERIFY_HTML = ('<h1>标题</h1>\n<blockquote style="color:#57606a;">'
                   '我去核了一遍：所给原文中不存在任何成交记录</blockquote>'
                   '\n<p>正文</p>')

    def test_verify_note_blocked_in_md_quote(self):
        self.assertIn("内部核验注记",
                      self.rules("bilibili", self.VERIFY_MD, "block"))

    def test_verify_note_blocked_in_html_blockquote(self):
        self.assertIn("内部核验注记",
                      self.rules("wechat", self.VERIFY_HTML, "block"))

    def test_verify_note_blocked_as_plain_text(self):
        """平台 draft/view 把引用块转成纯文本后 `> ` 前缀会消失，判据不能只认引用块。"""
        text = "标题\n我去核了一遍：所给原文中不存在该数字\n正文"
        self.assertIn("内部核验注记", self.rules("bilibili", text, "block"))

    def test_verify_note_blocks_on_all_platforms(self):
        """FIFO 四入口都不在 TECH_PLATFORMS，降级成 warn 就拦不住旧稿。"""
        for p, _ in S.PLATFORMS:
            self.assertIn("内部核验注记",
                          self.rules(p, self.VERIFY_MD, "block"), p)

    def test_html_comment_verify_note_is_not_blocked(self):
        """生成器的固定尾巴在 `<!-- -->` 里、不渲染；256/293 篇产物都带它。"""
        text = ("# 标题\n\n正文。\n\n<!--\n核对用来源（发布前逐个点开确认，别进正文）：\n"
                "  · 官网 <https://example.com>\n-->\n")
        self.assertNotIn("内部核验注记",
                         self.rules("wechat", text), "注释被当成正文，会误伤整批新稿")

    def test_verify_note_other_phrases(self):
        for phrase in ("所给原文中不存在", "无任何来源", "查无实据", "核对用来源"):
            self.assertIn("内部核验注记",
                          self.rules("xhs", "# 标题\n\n" + phrase + "。\n"),
                          phrase)

    def test_strip_marks_is_deterministic(self):
        out = G.strip_pipeline_marks(self.WATER + self.CANNED.split("\n")[2])
        self.assertNotIn("平台：", out)
        self.assertNotIn("TrustMRR 支付验证页", out)

    def test_money_without_timepoint_warns(self):
        """引了 MRR 却没说「这是哪天的数」⇒ warn（不能 block，存量太多）。"""
        text = "# 标题\n\n它的 MRR 是 $3,876，近 30 天收入 $1,780。\n"
        fs = [f for f in G.scan_content("csdn", text) if f["rule"] == "数据缺时点"]
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["level"], "warn")

    def test_timepoint_row_silences_the_warning(self):
        text = ("# 标题\n\n| 官方口径 | MRR $3,876 |\n"
                "| 数据时点 | 截至 2026-11-04（抓取日快照）｜口径：MRR |\n")
        self.assertNotIn("数据缺时点",
                         {f["rule"] for f in G.scan_content("csdn", text)})

    def test_handwritten_date_also_counts(self):
        """手写「截至 2026-11-04」也算 —— 判据认意思，不认某一行格式。"""
        text = "# 标题\n\n年化收入 $46K，截至 2026-11-04 抓取。\n"
        self.assertNotIn("数据缺时点",
                         {f["rule"] for f in G.scan_content("csdn", text)})

    def test_sync_date_phrasing_counts(self):
        """「2026-10-14 同步」也是时点。

        🚨 回归锁：第一版判据只认「数据时点 / 截至」，把散稿里最常见的
        「（TrustMRR 从支付网关直读，2026-10-14 同步）」全判成缺时点 ——
        草稿 17 篇全误报、产物 261 篇里 202 篇是误报（真缺的只有 59 篇）。
        """
        text = ("# 标题\n\nMRR $2,219、近 30 天收入 $2,453"
                "（TrustMRR 从支付网关直读，2026-10-14 同步）。\n")
        self.assertNotIn("数据缺时点",
                         {f["rule"] for f in G.scan_content("csdn", text)})

    def test_snapshot_time_phrasing_counts(self):
        text = "# 标题\n\n累计收入 $1.27M（快照时点 2026-09-11，之后仍会变）。\n"
        self.assertNotIn("数据缺时点",
                         {f["rule"] for f in G.scan_content("csdn", text)})

    def test_date_alone_without_metric_is_quiet(self):
        """只有日期没有营收数字 ⇒ 不报（判据是「数字缺时点」，不是「缺数字」）。"""
        text = "# 标题\n\n它 2023 年 9 月上线，2026-10-14 还在更新。\n"
        self.assertNotIn("数据缺时点",
                         {f["rule"] for f in G.scan_content("csdn", text)})

    def test_body_without_money_metric_is_quiet(self):
        """没引营收类数字就不该报 —— 否则游戏稿/纯工程稿会被天天误报。"""
        text = "# 标题\n\n它只收了 576 个付费用户，团队 1 人。\n"
        self.assertNotIn("数据缺时点",
                         {f["rule"] for f in G.scan_content("csdn", text)})


# ---------------------------------------------------------------- 幂等

class Dedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="guard_dedupe_")
        self.db_path = os.path.join(self.tmp, "ledger.db")
        self.sync_path = os.path.join(self.tmp, "longtail_sync.json")
        self._env = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = self.db_path
        c = sqlite3.connect(self.db_path)
        c.execute("""CREATE TABLE platform_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, platform TEXT,
            state TEXT, remote_id TEXT, draft_id TEXT, title TEXT,
            attempt INTEGER, published_at TEXT, updated_at TEXT, note TEXT, url TEXT)""")
        c.commit()
        c.close()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._env
        db.close_conn()
        for f in (self.db_path, self.sync_path):
            if os.path.isfile(f):
                os.remove(f)
        os.rmdir(self.tmp)

    def _add(self, case, platform, state, **kw):
        c = sqlite3.connect(self.db_path)
        c.execute("INSERT INTO platform_posts (case_id,platform,state,remote_id,draft_id,updated_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (case, platform, state, kw.get("remote_id"), kw.get("draft_id"),
                   "2026-11-04 20:00"))
        c.commit()
        c.close()

    def _sync(self, rows):
        with open(self.sync_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)

    def test_no_record_ok(self):
        self.assertTrue(G.check_dupe("csdn", "brand-new")["ok"])

    def test_published_blocked(self):
        self._add("chessigma", "csdn", "published", remote_id="169850647")
        r = G.check_dupe("csdn", "chessigma")
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], "published")

    def test_draft_blocked(self):
        self._add("chessigma", "tencentcloud", "draft", draft_id="340532")
        r = G.check_dupe("tencentcloud", "chessigma")
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], "draft")

    def test_draft_reuse_allowed_for_cloud_with_explicit_id(self):
        """两朵云「发已有草稿」的正道：显式给了 draft id ⇒ 复用，不再拦。"""
        for plat in ("aliyun", "tencentcloud"):
            self._add("reusecase", plat, "draft", draft_id="1711205")
            r = G.check_dupe(plat, "reusecase", allow_existing_draft=True)
            self.assertTrue(r["ok"], "%s 应放行草稿复用" % plat)
            self.assertEqual(r["level"], "draft-reuse")

    def test_draft_reuse_does_not_open_other_platforms(self):
        """默认 False：别的平台看到草稿记录仍按「别建第二份」拦。"""
        self._add("reusecase", "csdn", "draft", draft_id="1")
        self.assertFalse(G.check_dupe("csdn", "reusecase")["ok"])
        self.assertFalse(
            G.check_dupe("csdn", "reusecase", allow_existing_draft=True)["ok"])

    def test_published_still_blocked_when_reuse(self):
        """复用只对 draft 态开口；published / auditing 仍硬拦。"""
        for state in ("published", "auditing"):
            self._add("reusecase", "aliyun", state, remote_id="1789999")
            r = G.check_dupe("aliyun", "reusecase", allow_existing_draft=True)
            self.assertFalse(r["ok"], "%s 不该被复用放行" % state)
            self.assertEqual(r["level"], "published")

    def test_json_batch_not_blocking_when_reuse(self):
        """longtail_sync 的建稿批次就是「草稿已存在」的证据 ⇒ 复用时降级为提示。"""
        self._sync({"reusecase-2026-11-01-1": {
            "case": "reusecase",
            "platforms": {"aliyun": {"ok": True, "draftId": "1711205"}}}})
        r0 = G.check_dupe("aliyun", "reusecase", sync_path=self.sync_path)
        self.assertFalse(r0["ok"])
        self.assertEqual(r0["level"], "json")
        r = G.check_dupe("aliyun", "reusecase", sync_path=self.sync_path,
                         allow_existing_draft=True)
        self.assertTrue(r["ok"])

    def test_other_platform_not_blocked(self):
        self._add("chessigma", "csdn", "published", remote_id="169850647")
        self.assertTrue(G.check_dupe("zhihu", "chessigma")["ok"],
                        "跨平台分发是设计，不该被本平台记录拦住")

    def test_game_id_alias_blocked(self):
        self._add("ep-001-x", "cnblogs", "published", remote_id="23546485")
        self.assertFalse(G.check_dupe("cnblogs", "ext-ep-001-x")["ok"])

    def test_json_batch_fallback_blocked(self):
        # 模拟 11-04 实况：同一 case 同平台追加了两个建稿批次
        self._sync({
            "chessigma-2026-11-01-2": {"case": "chessigma",
                                       "platforms": {"tencentcloud": {"ok": True}}},
            "chessigma-2026-11-02-1": {"case": "chessigma",
                                       "platforms": {"tencentcloud": {"ok": True}}},
        })
        r = G.check_dupe("tencentcloud", "chessigma", sync_path=self.sync_path)
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], "json")
        self.assertIn("2 个建稿批次", r["detail"])

    def test_force_only_bypasses_dedupe(self):
        self._add("chessigma", "csdn", "published", remote_id="1")
        self.assertFalse(P._gate_dedupe("csdn", "chessigma", None)[0])
        ok, detail = P._gate_dedupe("csdn", "chessigma", None, force=True)
        self.assertTrue(ok)
        self.assertIn("--force", detail)


# ---------------------------------------------------------------- 游戏仓判重（B站二号）

class DedupeGamesRepo(unittest.TestCase):
    """二号的真账簿在**游戏仓**（`publish/bridge.py --platform bilibili2`）。

    本仓 SQLite / longtail 两路对它永远查不到 —— 2026-11-06 之前 `dedupe`
    对二号恒「无已发记录」，只能靠候选池先排除 + bridge 的
    `blocks_republish` 兜底，闸在纸上不在路上。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="guard_games_")
        self.db_path = os.path.join(self.tmp, "ledger.db")
        self.games_path = os.path.join(self.tmp, "publish_state.json")
        self._env = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = self.db_path
        db.close_conn()

    def tearDown(self):
        if self._env is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._env
        db.close_conn()
        for name in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, name))
        os.rmdir(self.tmp)

    def _games(self, slug, platform="bilibili2", state="published", **kw):
        d = {"articles": {slug: {"title": "x", "platforms": {
            platform: dict({"state": state}, **kw)}}}}
        with open(self.games_path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)

    def test_已发布就拦(self):
        self._games("ep-003-freedoom", remote_id="53787305")
        r = G.check_dupe("bilibili2", "ext-ep-003-freedoom",
                         games_path=self.games_path)
        self.assertFalse(r["ok"])
        self.assertEqual(r["level"], "games")
        self.assertIn("53787305", r["detail"])

    def test_已有草稿不拦但要说清(self):
        """`draft` 建稿会按标题就地复用，所以「箱里有这一篇」正是要发的那篇。

        拦它会把二号箱里 8 条陈旧稿永远锁死（档期选到就 block）。
        """
        self._games("ep-004-quake", state="drafted", remote_id="376917")
        r = G.check_dupe("bilibili2", "ext-ep-004-quake",
                         games_path=self.games_path)
        self.assertTrue(r["ok"])
        self.assertIn("就地复用", r["detail"])
        self.assertIn("376917", r["detail"])

    def test_产物id带b2后缀也认得出(self):
        self._games("ep-004-quake", remote_id="376917")
        for case in ("ext-ep-004-quake", "ext-ep-004-quake-b2", "ep-004-quake"):
            self.assertFalse(
                G.check_dupe("bilibili2", case, games_path=self.games_path)["ok"],
                "%s 应命同一条" % case)

    def test_台账缺失也要说出来(self):
        r = G.check_dupe("bilibili2", "ext-ep-004-quake",
                         games_path=os.path.join(self.tmp, "nope.json"))
        self.assertTrue(r["ok"], "读不到账不许当「有问题」硬拦")
        self.assertIn("缺失", r["detail"], "但也不许说成「无记录」")

    def test_一号不读游戏仓(self):
        """游戏仓只记二号；一号的判重仍走本仓台账。"""
        self._games("ep-003-freedoom", remote_id="53787305")
        self.assertTrue(G.check_dupe("bilibili", "ext-ep-003-freedoom",
                                     games_path=self.games_path)["ok"])

    def test_别的状态不算发过(self):
        self._games("ep-004-quake", state="failed")
        self.assertTrue(G.check_dupe("bilibili2", "ext-ep-004-quake",
                                     games_path=self.games_path)["ok"])


# ---------------------------------------------------------------- 唯一约束 / upsert

class UniqueLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="guard_db_")
        self.db_path = os.path.join(self.tmp, "ledger.db")
        self._env = os.environ.get("LEDGER_DB")
        os.environ["LEDGER_DB"] = self.db_path
        db.close_conn()

    def tearDown(self):
        db.close_conn()
        if self._env is None:
            os.environ.pop("LEDGER_DB", None)
        else:
            os.environ["LEDGER_DB"] = self._env
        if os.path.isfile(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.tmp)

    def test_unique_index_exists(self):
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='platform_posts' AND sql LIKE '%UNIQUE%'").fetchall()
        self.assertTrue(rows, "platform_posts 缺 (case_id, platform) 唯一索引")
        db.close_conn()

    def test_duplicate_insert_rejected(self):
        conn = db.get_conn()
        db.upsert_post(conn, "same-case", "csdn", {"state": "draft"})
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO platform_posts (case_id,platform,state) "
                         "VALUES ('same-case','csdn','draft')")
        conn.rollback()
        db.close_conn()

    def test_upsert_keeps_id_and_does_not_downgrade(self):
        conn = db.get_conn()
        db.upsert_post(conn, "c1", "csdn", {"state": "published",
                                            "remote_id": "169850647"})
        conn.commit()
        first = db.get_post(conn, "c1", "csdn")
        db.upsert_post(conn, "c1", "csdn", {"state": "draft", "note": "迟到的建稿回写"})
        conn.commit()
        second = db.get_post(conn, "c1", "csdn")
        self.assertEqual(first["id"], second["id"], "upsert 不该换 id")
        self.assertEqual(second["state"], "published", "draft 不许盖掉 published")
        self.assertEqual(second["remote_id"], "169850647", "UPDATE 不该冲掉没带的字段")
        db.close_conn()


# ---------------------------------------------------------------- 封面 / 命令

class CoverAndCmd(unittest.TestCase):
    def test_missing_cover_blocked(self):
        r = G.check_cover("tencentcloud", "no-such-case-xyz")
        self.assertFalse(r["ok"])

    def test_cover_remote_never_claims_ok_without_record(self):
        """没有回读记录 ⇒ 只能出 warn，绝不能变成「✓ 封面没问题」。"""
        fs = G.check_cover_remote("csdn", "no-such-case-xyz")
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]["level"], "warn")
        self.assertNotEqual(fs[0]["level"], "block")   # 没查过 ≠ 有问题

    def test_cover_remote_tells_why_for_non_readback_platform(self):
        fs = G.check_cover_remote("wechat", "any-case")
        self.assertEqual(fs[0]["rule"], "脚本不设封面")
        self.assertIn("封面", fs[0]["detail"])

    def test_audit_has_both_cover_gates(self):
        """④ 静态（本地有没有图）与 ⑤ 动态（远端有没有）必须都在汇总里。"""
        res = G.audit("csdn", "no-such-case-xyz")
        self.assertIn("cover", res)
        self.assertIn("cover_remote", res)

    def test_cmd_carries_cover_for_file_platforms(self):
        # 对 COVER_ARG_PLATFORMS，命令里必须出现 --cover；用真实存在的封面
        for p in G.COVER_ARG_PLATFORMS:
            with self.subTest(platform=p):
                case = next((c for c in ("chessigma", "cod-dex", "mort")
                             if G.cover_of(c)), None)
                self.assertIsNotNone(case, "需要一个有封面的真实 case 做夹具")
                pack = os.path.join(ROOT, "drafts", "newbatch", p, case + ".md")
                cmd = P.build_cmd(p, case, pack, True)
                self.assertIn("--cover", cmd, "%s 发布命令没带封面" % p)

    def test_gate_cover_rejects_missing_cover(self):
        ok, _ = P._gate_cover("tencentcloud", "no-such-case-xyz", None)
        self.assertFalse(ok)


# ---------------------------------------------------------------- 生成器

class Generator(unittest.TestCase):
    CASE = {
        "id": "demo", "name": "Demo",
        "one_liner": "一个把公开招聘页聚合起来并按规则打分的小工具。",
        "what_it_does": "抓 5 万个招聘页，按变更率分频调度，再做复合键去重。",
        "how_it_makes_money": "按订阅收费，公开口径只披露活跃订阅数。",
        "verdict": "维护成本落在采集侧，不是模型侧。",
        "metrics": {"headline": "截至 2026-09-30 活跃订阅 2",
                    "growth": "上线 5 天做到 18,380 美元 MRR"},
        "playbook": ["按变更率分频", "去重键要稳"],
        "why_it_works": ["变更率可测", "失败退避防雪崩"],
    }

    def test_no_hard_cut(self):
        long_case = dict(self.CASE, one_liner="一个非常非常长的产品描述" * 10)
        t = GM.make_title(long_case, platform="cnblogs")
        self.assertLessEqual(len(t), G.TITLE_MAX["cnblogs"])
        self.assertFalse(t.endswith("的") or t.endswith("，"),
                         "标题不该以半个词收尾：%s" % t)

    def test_no_watermark_or_canned_source(self):
        for plat, b in GM.BUILDERS.items():
            txt = b(self.CASE)
            self.assertNotIn("平台：", txt, "%s 还在写管线水印" % plat)
            self.assertNotIn("TrustMRR 支付验证页", txt, "%s 还在写罐头来源" % plat)
            self.assertNotIn("数字带口径带修正", txt)

    def test_five_platforms_have_distinct_skeletons(self):
        heads = {}
        for plat, b in GM.BUILDERS.items():
            import re
            heads[plat] = tuple(re.findall(r"^##\s+(.+)$", b(self.CASE), re.M))
        for a in heads:
            for b in heads:
                if a >= b:
                    continue
                self.assertNotEqual(heads[a], heads[b],
                                    "%s 与 %s 的栏目骨架完全相同" % (a, b))

    def test_generated_body_passes_template_gate(self):
        import re
        for plat, b in GM.BUILDERS.items():
            txt = b(self.CASE)
            blocks = [f for f in G.scan_content(plat, txt)
                      if f["level"] == "block"]
            self.assertEqual(blocks, [], "%s 生成稿被 template 闸拦：%s" % (plat, blocks))

    def test_tencentcloud_title_has_no_money(self):
        t = GM.make_title(self.CASE, money=False, platform="tencentcloud")
        self.assertNotIn("18,380", t)


if __name__ == "__main__":
    unittest.main(verbosity=2)
