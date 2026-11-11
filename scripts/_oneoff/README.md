# _oneoff/ —— 一次性排障脚本归档（**已入库**）

这里放的是排障 / 抢修 / 探针期间写下的一次性脚本（`_diag_*` / `_probe_*` /
`_fix_*` / `_xhs_*` / `_tt_*` / `_verify_*` / `aliyun_*` / `clean_*` …）。它们
**都不是流水线的一部分**，只记录当时的现场；跑之前先读代码确认前提（端口、
profile、数据版本、当天台账状态）。

## ⛔ 为什么这些文件必须入库

2026-10-18 曾经做过相反的决定：把整个归档加进 `.gitignore`，README 写着
「需要复用时 `mv` 回来，或从当日 memory / git 历史里抄」。结果——

- `docs/PRD.md` 把这些脚本当作「复现证据」引用（28 处），而脚本不在仓库里；
  换机 / 重装 / clone 之后凭据失效，同一个修复被**重写一遍**。
- `rewrite_yun_draft.py` 就是活例子：先写在 `tmp/`，修完才发现没入库，
  又花一轮把它挪进 `scripts/`。

所以 2026-11-11 反转该决定：**归档入库**。`tmp/` 只允许放产物（截图、抓包、
备份 json、日志），脚本一律放这里或 `scripts/` 根目录。

## 约定

| 项 | 规定 |
|---|---|
| 路径深度 | `scripts/_oneoff/x.py` 比 `tmp/x.py` 深一层 ⇒ `ROOT` 必须是**上跳三层**：`os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`，或 `Path(__file__).parent.parent.parent`。写错会静默指到 `scripts/`。 |
| 新脚本命名 | 带日期后缀（`fix_xxx_20261111.py`），便于和 PRD 条目对应 |
| 产物落盘 | 一律写 `ROOT/tmp/`（目录已存在且不入库）；**别**把产物放本目录 |
| 入档校验 | 新增文件后跑 `python -X utf8 scripts/check_tmp_leaks.py`，再跑下面的 ROOT 自检 |
| 复用方式 | `git log --follow -- scripts/_oneoff/<name>.py` 看当时改了什么，别凭记忆重写 |

## 🛟 又写进 tmp 了？一条命令捞回来

```bash
python -X utf8 scripts/check_tmp_leaks.py --archive --dry-run   # 先看会搬什么
python -X utf8 scripts/check_tmp_leaks.py --archive             # 真搬 + git add
```

它会：搬进本目录 → `.py` 自动把 `ROOT` 上跳层数 **+1** → 用新 `__file__` 校验
（解析不到仓库根就**原样退回 tmp**，不会把坏文件塞进仓库）→ `git add`。
重名不覆盖（自动加 `__2` 后缀）；内容完全相同的副本判为重复并舍掉源。
加 `--include-notes` 可顺带收 `tmp/` 顶层的 `.md` 笔记（默认只收脚本）。

**还有一道自动闸**：仓库带了 `.githooks/pre-commit`，`tmp/` 里出现新脚本时
`git commit` 直接失败并提示上面那条命令。启用一次即可（每个 clone 各一次）：

```bash
git config core.hooksPath .githooks
```

## ROOT 自检（新增文件后必跑）

```bash
python -X utf8 - <<'PY'
import sys; from pathlib import Path
sys.path.insert(0, "scripts/_oneoff")
from migrate_tmp_to_oneoff_20261111 import verify_root
bad = []
for p in sorted(Path("scripts/_oneoff").glob("*.py")):
    got, err = verify_root(p)
    if got is None and err and "未定义" not in err:
        bad.append((p.name, err))
print("ROOT 异常:", bad or "无")
PY
```

`未定义 ROOT` 是允许的（纯探针可能只用相对路径）；解析出来**不等于仓库根**才是错误。

## 迁移记录

- 2026-10-18：112 个脚本从 `scripts/` 根目录挪进本归档，同时被 `.gitignore` 挡住。
- 2026-11-11：取消忽略；把 `docs/` 引用到的 60 个 `tmp/*.py` 全部迁入本目录
  （`migrate_tmp_to_oneoff_20261111.py`，含 ROOT 层数 +1 与逐文件校验）；顺手修掉
  归档里 15 个 `ROOT` 误指 `scripts/` 的老脚本。
- 2026-11-11 晚：`check_tmp_leaks.py` 从「只拦」升级为「能捞」——
  新增 `--archive` 与 `.githooks/pre-commit`（PRD #139）。至此 `tmp/` 里
  新出现的脚本，**不归档就提交不了**，且归档一步到位。

**保留在 `scripts/` 根目录的（不在本归档里）：**

| 文件 | 为什么留 |
|---|---|
| `_launch_9222.py` | 公众号 Chrome 启动器（活工具，preflight 会提示它） |
| `_launch_9223.py` | 头条 / 小红书 Chrome 启动器（活工具） |
| `_t_bili_parse.py` `_t_coverdesc.py` `_t_hold.py` | **已入库的真回归测试**，不是临时脚本 |
