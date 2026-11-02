#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台适配器：把统一的 Article 变成「某个平台存草稿」这一步。

刻意做成**薄壳**：所有 CDP 选择器、封面上传、草稿箱点击都留在既有的
`toutiao_publish.py` / `bilibili_publish.py` / `xhs_publish.py` /
`wechat_publish.py` 里（那是几个月的踩坑沉淀，不能为了架构好看重写）。
掘金是唯一的例外：`juejin_publish.py` 不走 CDP，纯 REST，见该脚本头部说明。
适配器只负责四件事：

1. 声明自己是谁（key、显示名、需要什么产物、标题去哪个平台取精修版）；
2. 跑 build（缺稿才造，幂等）；
3. 跑 publish（存草稿 or 真发）；
4. 把子进程结果翻译成清单里的状态。

这正是 Wechatsync 的平台适配器思路，只是执行引擎从「扩展里的浏览器」
换成本项目的「CDP + 已登录 Chrome」。
"""

# ⛔ 2026-11-02 起**停用掘金**，不再发布任何文章。
# 停用原因（不是技术故障，是平台侧不可预期）：
#   同账号 10 篇已发文章只有 2 篇过审（通过率 20%），且**不存在可归因的
#   客观标准**——标题模板化、八节骨架、第一人称踩坑体例三种写法都试过，
#   被拒与通过的差异无法从内容侧复现或预测。对一个「发出去就消失、
#   只能靠猜」的渠道继续投入，性价比不成立。
#   ⇒ 结论：这是渠道问题，不是稿件问题。不要再花时间调文体去撞审核。
#
# 恢复方法：把下面 juejin 加回 import 即可（adapter 文件本身完整保留）。
# 另注：`scripts/multiplatform/blocked.py` 里仍保留 juejin 分支 —— 那是
#   **清理存量草稿**（27 条）用的，不属于发布路径，故不随本次停用一起摘掉。
from . import toutiao, bilibili, xiaohongshu, wechat  # noqa: F401
# from . import juejin   # ⛔ 已停用，见上
from .base import REGISTRY, Adapter, get, all_adapters     # noqa: F401

