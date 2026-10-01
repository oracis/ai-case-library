# -*- coding: utf-8 -*-
"""探测「现在哪些免费 LLM 真能用」—— **已迁移到独立项目 free-llm-probe**。

本文件自 2026-10-01 起只是一层**薄包装**，不再自带探测实现。
原因：探测逻辑（认证方式、错误分类、档案产出）在两个项目里各存一份，
迟早会漂移 —— 消费方读到旧结论比没有结论更糟。

真相在隔壁：

    ../free-llm-probe/probe.py                  零依赖探测器
    ../free-llm-probe/serve.py                  可选 HTTP 服务
    ../free-llm-probe/docs/INTEGRATION.md       接入文档

用法
----
    # 探一次并产出档案（老命令照常能用，内部转发到独立项目）
    python scripts/probe_free_llm.py
    python scripts/probe_free_llm.py --models space-bunny-free

    # 之后核验脚本直接吃档案
    python scripts/ai_verify.py \
        --llm-profile ../free-llm-probe/out/profiles.json --ping

实测结论（2026-10-01，别再自己推一遍）
----------------------------------------
- OpenCode Zen 上名字带 `free` 的有 11 个，免 key 真能调通的**只有 1 个**：
  `space-bunny-free`。
- 其余 10 个：6 个 `FREE_TIER_LOCKED`（限 OpenCode 客户端内，伪造来源无效）、
  2 个 `REGION_BLOCKED`、1 个 `NOT_FOUND`、1 个 `UPSTREAM_5XX`。
- 免 key 不是「不传 Authorization」，而是发**空 Bearer**：
  `Authorization: Bearer `。省略整个头会挂到超时；填 `Bearer free` 反而 401。
⇒ 「模型名带 free」不能当可用性证据，**必须逐个发真实请求**。

⚠ **别用 /models 当可用性判据**：它免 key 就能列出全部 84 个，
包括 10 个现在根本调不通的。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_PROJECT = os.path.normpath(
    os.path.join(HERE, os.pardir, os.pardir, "free-llm-probe"))
PROBE_PY = os.path.join(PROBE_PROJECT, "probe.py")


def main(argv):
    if not os.path.isfile(PROBE_PY):
        sys.stderr.write(
            "✗ 找不到独立探测项目：%s\n"
            "  探测能力已迁移到 free-llm-probe（不在本仓库内）。\n"
            "  拿到那个项目后放回同级目录，或直接跑它：\n"
            "    python free-llm-probe/probe.py --emit-profiles\n" % PROBE_PY)
        return 2
    # 转发所有参数（含 --key）。档案由独立项目写到它自己的 out/ 下。
    cmd = [sys.executable, "-X", "utf8", "-u", PROBE_PY] + list(argv)
    sys.stderr.write("→ 转发到独立项目：%s\n" % PROBE_PROJECT)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
