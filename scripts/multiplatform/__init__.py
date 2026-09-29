#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一多平台发布层。

设计参考（2026-09-29 读完后定稿，见 docs/MULTIPLATFORM.md）：

* **Wechatsync**（Chrome 扩展）值得抄的是「用本机浏览器登录态 + 草稿优先 +
  统一核心 + 平台适配器 + CLI/多入口」，不抄它的扩展形态（本项目全是本地
  Python 脚本，塞进 MV3 扩展反而多一层无谓的打包与权限申请）。
* **CSDN 那篇**（Node + TS）值得抄的是「统一 Article 模型 + platforms/*.ts
  适配器 + 配置决定启用平台 + 顺序调度并记录结果」，**不抄它的调度**：
  示例没有状态持久化、没有重试、没有幂等，`pending` 会被当成成功，平台失败
  会被部分成功掩盖 —— 这正是本项目批量发 37 条时踩过的坑。

所以本层只做三件事，不碰任何平台内部的 CDP 实现：

1. `article.py`  统一 Article 模型（把一篇 case 变成各平台都能用的输入）
2. `state.py`    发布清单 + 状态机 + 幂等 + 断点续传（补 CSDN 缺的那半）
3. `adapters/`   平台适配器（薄壳，调用既有的 *_publish.py 子命令）

零第三方依赖，和项目其余部分一致。
"""

__all__ = ["article", "state", "runner", "adapters"]
