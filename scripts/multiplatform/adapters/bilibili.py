#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站专栏适配器。

与其它平台四点不同，写在这里免得每次重新踩：

1. **没有状态文件**。草稿只存在 B站草稿箱里，本地无从判断哪条发过。
   所以清单（`data/publish_manifest.json`）是唯一的幂等依据 —— 这也是
   必须有 state 层的原因，不是为了架构好看。
2. **发布分两步**：默认 `publish` 只**存草稿**（`draft/add`）；给 `--yes`
   才是**真发布**，走 `send-draft --case <cid>` 点编辑器里的「发布」按钮
   （2026-10-20 实测可行，见 bilibili_publish.ui_publish_draft）。
   注意 B站发布走的是**草稿箱**而不是本地产物，所以 yes 分支传的是 cid，
   脚本按 cid 反查属于它的那条草稿（早期版本在这里只看「最久那条」）。
3. **LV1–3 每天只能发 1 篇**，LV4–6 5 篇 —— 额度用完发布按钮直接变灰，
   编辑器顶上写「已达到当日投稿上限，只能保存草稿哦~」。此时脚本明确报失败、
   不记账。⚠ 之前的错误结论是「headless 下恒 disabled（封面校验）」，实测
   封面设好了照样灰，真原因是额度（详见 bilibili_publish 的注释）。
4. **封面要单独一步**（`bilibili_publish.py cover`）。它复用头条的
   `out/toutiao/<id>/cover.png`，走「发布设置 → 自定义封面」开关 →
   hidden input + DataTransfer → 裁剪弹窗确定 → 存草稿。
   远端判据是 `image_urls/origin_image_urls`，**不是 `banner_url`**
   （后者在草稿接口里恒空，详见 bilibili_publish._cover_of）。
5. **批量必须走脚本自带的 `publish --only/--retries`**，不要逐条 spawn。
   逐条 spawn 每条都要重起 CDP、重开草稿箱 tab，37 条下来既慢又会把页面
   WebSocket 拖超时（2026-09-30 实测跑到第 2 条就 TimeoutError）。
   `--only` 是断点续传用：机器休眠/断连后照着上轮日志的编号补跑剩下的。
"""

import os

from .base import ROOT, Adapter, register


@register
class Bilibili(Adapter):
    key = "bilibili"
    label = "B站专栏"
    script = "bilibili_publish.py"
    title_from = "bili"
    requires = ("out/bili/<id>/article.html", "out/toutiao/<id>/cover.png")
    supports_publish = True           # 有 --yes 才真发（走 send-draft，见下）
    supports_replace = True
    supports_cover = True
    supports_only = True
    has_state_file = False

    def done_ids(self):
        """B站侧只能靠清单；这里刻意返回空，让 runner 用 manifest 判幂等。"""
        return set()

    def publish(self, art, dry=False, replace=False, yes=False):
        """B站的发布入口与存草稿入口**不是同一个子命令**。

        基类把发布实现成 `publish --case X [--yes]`，但 `bilibili_publish.py`
        的 `publish` 是**存草稿**（`draft/add`），且**没有 `--yes` 这个参数**
        —— 硬拼上去的结果是 argparse 直接「unrecognized arguments」退出 2。
        真发布必须走 `send-draft --case X`（编辑器 UI 点「发布」）。
        """
        if yes:
            return self.run(["send-draft", "--case", art.cid], not dry,
                            timeout=420)
        return super().publish(art, dry=dry, replace=replace, yes=False)

    def cover(self, art, dry=False, force=False):
        """给已存草稿补封面。幂等：远端已有封面的会自己跳过。"""
        args = ["cover", "--case", art.cid]
        if force:
            args.append("--force")
        return self.run(args, not dry, timeout=600)

    def parse_batch(self, out):
        """从批量 stdout 里解析每条的成败。

        为什么不能只看退出码：批量「37 条里挂了 3 条」整体是失败，但那 34 条
        其实已经存好了 —— 按退出码会把它们全标failed，下次又重发一遍。

        ⚠⚠ **判据不能绑死某一个前缀**（2026-10-05 实测踩中）：
        脚本的输出格式变过 —— 早期是 `✓ 标题《…》`，现在（API 路径）是
        `✓ API 新建草稿 aid=343012《…》正文 1246 字 / 22 段`。
        `parse_batch` 只认 `s.startswith("✓ 标题")` ⇒ 两条真存好的草稿
        全被判 failed，台账写 failed、队列反复重发 ⇒ **远端多出重复草稿**
        （这正是记忆铁律里 B站「远端 38→101 条副本」那类事故的配方）。

        所以判据改成「**行首 ✓ 且不是纯提示行**」，覆盖
        `✓ 标题《…》` / `✓ API 新建草稿 …` / `✓ 已存草稿 …` 三种历史格式。
        ⚠ 不能额外要求行内出现「草稿」二字 —— 回归测试立刻抓到反例：
        `✓ 标题《MORT》正文 900 字`（早期格式）就没有「草稿」，
        加上这层过滤会把已成功的判成失败（实测 test_legacy_title_prefix红）。
        真正的失败信号是 `✗`，或脚本自己打的「失败 N 条：…」。
        失败清单脚本会自己打印「失败 N 条：a b c」，那个更准，优先用。

        ⚠⚠ **「新一条开始」的判定必须是 `[N/M] <cid>` 这一种形状**
        （2026-10-11 实测踩中）。旧写法 `s.startswith("[") and "]" in s`
        太松 —— 脚本启动时打的 `[autostart] Chrome 已启动
        （Chrome for Testing, --no-sandbox）port=9223 profile=…` 也以 `[` 开头、
        含 `]`，于是在**还没开始处理任何一条**时就被当成「新一条」，
        cur 被设成 `autostart`，结束时结算 ⇒ **7 条全掉进 failed，且失败
        列表里多出一个叫 `Chrome` 的假 id**（实测：done 6 条 / failed
        `['Chrome', 'wpconvert-ai-convert-ai-sites-to-wordpress']`，
        而 7 条其实全部 `✓ API 新建草稿 aid=…` 真存好了）。
        ⇒ 台账写 failed ⇒ 队列反复重发 ⇒ 远端多出重复草稿。
        修法：只认 `[数字/数字]` 这种进度条形状，其它方括号行一律当提示行跳过。
        """
        import re
        MARK_RE = re.compile(r"^\[\d+/\d+\]\s+(\S+)")
        done, failed = set(), set()
        cur = None
        ok_flag = False
        for ln in (out or "").splitlines():
            s = ln.strip()
            m = MARK_RE.match(s)
            if m:
                # 新一条开始：先结算上一条
                if cur is not None:
                    (done if ok_flag else failed).add(cur)
                cur = m.group(1)
                ok_flag = False
            elif s.startswith("✓") and cur:
                ok_flag = True
            elif s.startswith("✗") and cur:
                ok_flag = False
            elif s.startswith("失败 ") and "条：" in s:
                # 脚本给的失败清单最准，直接覆盖
                for x in s.split("条：", 1)[1].replace(",", " ").split():
                    failed.add(x.strip())
                    done.discard(x.strip())
        if cur is not None:
            (done if ok_flag else failed).add(cur)
        return done, failed

    def batch(self, cids, dry=False, replace=False, retries=2):
        """一个进程内串行跑多条。

        ⚠ 关键：一条一条 spawn 会因为每条都重开 CDP/草稿箱 tab 而把页面
        WebSocket 拖超时，且中途断连后没有断点续传信息。脚本内的
        `publish --only a,b,c --retries N` 都处理了。
        不传 cids 时是「全部」——注意那是脚本的 list_ids() 全集，不是
        runner 筛出来的「未完成集」，所以调用方必须显式给 cids。
        """
        if not cids:
            return None
        args = ["publish"]
        if replace:
            args.append("--replace")
        args += ["--retries", str(retries), "--only", ",".join(cids)]
        # 每条约 30-60s，留足余量；full=True 拿完整 stdout 逐条判定成败
        return self.run(args, not dry, timeout=900 * len(cids), full=True)
