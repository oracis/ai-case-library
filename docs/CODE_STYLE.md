# 代码格式规范（Python）

> 一句话：**PEP 8 打底，Google 风格指南补充，本仓库实测惯例收口；新代码照此写，存量代码不回溯重写。**
>
> 本文 2026-10-20 成文。做法：先取两份权威规范（PEP 8 官方原文、Google 官方
> Python Style Guide），再对照 `scripts/` 下 179 个 `.py` 的实际写法逐条核对 ——
> 文中每条「仓库惯例」都带实测计数，不是拍脑袋的新规。

---

## 一、总纲与优先级

**权威来源（本规范的两个上游）：**

| 来源 | 地位 | 用途 |
|---|---|---|
| [PEP 8](https://peps.python.org/pep-0008/) | Python 官方风格指南（Guido van Rossum 等，2001 年起） | 布局/命名/注释/docstring 的默认答案 |
| [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) | google/styleguide（39.7k stars） | PEP 8 未覆盖处的补充答案（TODO 格式、Main 模式等） |

**冲突时的优先级（从高到低）：**

1. 本文件第三节「仓库特有硬规矩」—— 每条都是被事故逼出来的，优先级最高；
2. 现有代码的多数写法（本文件就是它的成文，见各条实测证据）；
3. PEP 8；
4. Google 风格指南。

**存量代码不强制重写。** PEP 8 自己给了正当忽略条款：「为与周边旧代码保持
一致」「代码早于规范引入」都是合法理由。改到哪个函数，顺手改成新规矩即可；
专门开「格式化 PR」不被鼓励（本仓库的历史证明那只会制造格式化噪音 diff）。

---

## 二、布局与命名（PEP 8 + Google，仓库实测一致）

### 2.1 缩进与行宽

| 项 | 规范 | 依据 |
|---|---|---|
| 缩进 | 4 空格/级，禁止 Tab，禁止混用 | PEP 8；实测全库 0 个 Tab |
| 行宽 | **≤ 100 字符** | 见下方「行宽为什么是 100」 |
| 空行 | 顶层函数/类之间 2 行；类内方法之间 1 行 | PEP 8，实测一致 |
| 一行一句 | 是 | PEP 8；Google 3.14 同 |

**行宽为什么是 100，不是 PEP 8 的 79：** PEP 8 原文留了团队协商条款
「可放宽到 99 字符，注释与 docstring 仍保持 72」；Google 的上限是 80，但对
长 import、URL、路径名、无空格长字符串常量、lint 抑制注释五类情形明确豁免。
本仓库中文注释密度极高 + 大量 URL/选择器字符串，实测 79–100 字符区间有
**1412 行**，超过 100 的只有 36 行（外加 `score_china_fit.py` 的数据表
167 行——那是数据不是代码）。取 100：中文注释按 Google 的精神尽量短，
实在长的（URL/选择器）按豁免处理，**不为了凑行宽把一条 URL 切成两行**。

### 2.2 引号与字符串

- 字符串统一**双引号**（实测 27,459 行含双引号字符串，单引号仅零星）。
- 三引号 docstring 一律 `"""`（PEP 8 3.1 节 docstring 条款；Google 同）。
- 格式化用 `%`，不用 f-string：实测 4,063 行 `%` vs 92 行 f-string。
  新代码跟多数。长模板用 `"""..."""` 包住的隐式续行（JS 片段见
  `bilibili_publish.py` 的 `JS_PUBLISH_STATE`），**不用反斜杠续行**
  （PEP 8 与 Google 3.2 一致）。

### 2.3 命名（PEP 8 命名表 + Google 3.16.2，实测一致）

| 对象 | 风格 | 仓库实例 |
|---|---|---|
| 模块/文件 | `lower_with_under.py` | `bili_draft_api.py` |
| 类 / 异常 | `CapWords`，异常以 `Error` 结尾 | `AdminError`、`TestParseBatch` |
| 函数/变量 | `lower_with_under`，私有加 `_` | `_load_manifest`、`is_done` |
| 常量 | `CAPS_WITH_UNDER` | `ROOT`、`PUB_PORT` |
| 测试文件 | `test_<被测对象>.py` | `test_bilibili_publish.py` |

- 禁止 `l`/`O`/`I` 单字符变量名（PEP 8）。
- 布尔判据函数用 `is_*` / `_is_*` 前缀（`is_published`、`_is_done`）。

### 2.4 导入（PEP 8 分组 + 本仓库 bootstrap 模式）

```python
import argparse
import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_publish as wp  # noqa: E402  复用 CDP / 案例加载
```

- 标准库一组在前；本地模块放最后，`sys.path` 引导行之后。
- 引导之后的本地导入每条带 `# noqa: E402` + **一句用途注释**
  （实测 207 处，不是可选装饰）。
- 禁止 `from x import *`（PEP 8）。

---

## 三、仓库特有硬规矩（优先级最高，每条都有事故编号）

### 3.1 文件头

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块一句话摘要。

结构化正文：为什么/形态确认（带日期）/用法示例/坑。
"""
```

- shebang 用 `python3`（实测 90:17 压倒性；Google 3.7 同样推荐
  `#!/usr/bin/env python3`）。仅程序主文件需要 shebang（Google 3.7），
  但本仓库 `scripts/` 全是入口脚本，**默认都写**。
- `# -*- coding: utf-8 -*-` 保留（147/179 个文件都有）。Python 3 虽默认
  UTF-8，但本仓库中文注释/docstring 密度全库最高（616 个 ⚠/🚨 标记），
  统一留着，别删。
- 模块 docstring 必须写「**为什么**」和「**用法**」，不只写「是什么」。
  范例：`ledger.py` 顶部（为什么要有台账层）、`bilibili_publish.py` 顶部
  （形态确认 + 用法清单 + 🚩 标记的重大变更）。

### 3.2 中文注释，带日期，解释「为什么」

- 注释与 docstring **用中文**。（这是对 PEP 8「注释用英语」的有意偏离：
  本仓库读者只有中文使用者，且踩坑记录的价值远大于国际化 —— 属于 PEP 8
  「正当忽略」条款第 1 条。）
- 踩过的坑用 `⚠` / `🚨` / `🚩` 标记，**必须带日期**（实测 616 处）：

  ```python
  # ⚠ 2026-10-19 修「台账历史被毁」：旧实现只取 id 列表，
  #    追加一条就把整本台账的 at 全部重写成当前时间。
  ```

- 注释解释「为什么这么写」，不解释「代码在干什么」。
- **事故教训必须写进代码注释，不能只写在 commit message 里** ——
  换人/换 LLM 接手时，注释是唯一还留在原地的东西。

### 3.3 写 JSON 数据文件：保格式 + 原子写

这是本仓库最容易踩的雷区，2026-10-20 一晚连踩两次（见第六节事故表）：

1. **缩进按原文件走**。本仓库 `data/*.json` 两种缩进并存：
   `publish_manifest.json` 是 1 空格（`indent=1`），其余多为 2 空格。
   **改一个文件的缩进 = git diff 多出上千行纯格式噪音。**
2. **行尾按原文件走**。`.gitattributes` 已强制 `* text=auto eol=lf`，
   但工作区两种行尾并存；写盘前先读原文件探测（`ledger._read_file`
   是范式），写错行尾 = 全文件 diff。
3. **末尾换行按原文件走**。`json.dumps` 产物不带末尾 `\n`，而有的文件
   历来以换行结尾 —— 判据见 `ledger._write_file` 的 `tail` 逻辑。
4. **Windows 文本模式会把 `\n` 翻成 `\r\n`** ⇒ 写文件必须显式
   `newline=""`（或 `newline="\n"`）。
5. **原子写**：临时文件 + `os.replace`。半截 JSON = 整本台账失效。
   范式：`multiplatform/state.py` 的 `Manifest.save`、
   `ledger._write_file`。
6. `ensure_ascii=False`（中文可读）；清单类文件加 `sort_keys=True`。

**改任何「写 data/*.json」的代码前，先读 `multiplatform/state.py` 和
`scripts/ledger.py` 的写盘部分。**

### 3.4 异常与退出

- 兜底 except 必须显式标注并留痕：

  ```python
  except Exception as e:                          # noqa: BLE001
      print("    ⚠ 清单写入失败：%s" % str(e)[:80])
  ```

  实测 215 处 `noqa: BLE001`。**禁止静默 `except: pass`** ——
  本仓库的「假绿」事故一半来自静默 swallow。
- CLI 参数/用法错误用 `raise SystemExit("人话说明")`（实测 121 处），
  不用 `print + sys.exit(1)` 两段式。

### 3.5 长任务打印带 `flush=True`

后台/长流程（起 Chrome、轮询回读）的进度打印必须 `flush=True`
（实测 106 个文件），否则后台任务日志几小时才吐一行，排障时抓瞎。

### 3.6 入口结构

```python
def main():
    ...

if __name__ == "__main__":
    main()
```

实测 172/179 个文件有此结构（Google 3.17 同款要求：模块要被 import 和
单测复用，顶层不能跑业务逻辑）。

### 3.7 TODO / 待办的写法

Google 3.12 要求 `# TODO(资源链接): 说明`。本仓库没有 bug 追踪系统，
等价物是：**带日期的 ⚠ 注释 + 「待办」字样**，且必须写清触发条件：

```python
# 待办：--fix-images 自动重传（删裂图 + CDP DOM.setFileInputFiles）。
```

禁止无日期、无条件的 `# TODO: 优化一下`。

---

## 四、测试规范

- 位置：`scripts/test_*.py`；跑法：`cd scripts && python -m unittest`
  （**本机没有 pytest 可用环境，别用 pytest**）。
- `unittest.TestCase`，类名 `CapWords`（实测 256 个测试类）。
- 纯函数优先，单测里**不连浏览器**；要连真机的，docstring 写明
  「不连浏览器」或标注真机验证状态（如 `_click_publish` 三套兜底策略
  「未经真机验证，第一次用必须 --dry」）。
- 断言三问（2026-10-19 用四条假红换来的）：量的是**人看到的**还是内部
  表示？是「没数据」还是「数据错了」？是多做了还是违规了？
- 「跳过」必须打出来 —— 静默 pass 是假绿的另一种。
- 新坑位/新功能：先写测试再跑全量门禁（根目录 `python selftest.py`
  122 项 + `scripts` 全量单测，全绿才算完）。

---

## 五、与两大规范的「有意偏离」清单

| 规范条款 | 本仓库做法 | 理由 |
|---|---|---|
| PEP 8：注释用英语 | 中文 | 读者只有中文使用者；坑位记录用中文才写得下去 |
| PEP 8：行宽 79 | 100 | PEP 8 自己的 99 协商条款 + Google 的豁免精神；实测 1412 行落在 79–100 |
| PEP 8/Google：TODO 带 bug 链接 | ⚠ + 日期 | 无 bug 追踪系统，日期+事故描述是等价物 |
| Google：f-string/% 不限制 | `%` | 存量压倒性多数（4063:92），一致性优先 |

---

## 六、反模式清单（全部是本库实际踩过的）

| 反模式 | 事故 |
|---|---|
| 写 JSON 不保缩进/行尾/末尾换行 | 2026-10-20：`indent=2` 重写清单，3156 行格式噪音进 diff（两处写盘入口同病） |
| 只取 id 重建台账，丢原字段 | 2026-10-14：整本台账 `at` 时间被抹成当前时间 |
| 静默 `except: pass` | 假绿（selftest 假红/假绿各若干） |
| 注释谎称行为（如「submit=1 直接发布」） | 2026-10-20 B站双重证伪，注释比没注释更坏 |
| 「接口里查不到」当成「线上没有」 | 小红书 `posted` 接口只返回最老 8 条 |
| 无日期 TODO / 无理由 noqa | 三个月后没人敢删也不敢信 |

---

## 七、自检

本仓库**不引入** flake8/ruff/pylint 配置（CI 只跑 selftest + unittest +
uitest，加 linter 是另一项决策，不在本文范围）。手工自检：

```bash
# 布局（可选，本机若装了 pycodestyle）
python -m pycodestyle --max-line-length=100 scripts/xxx.py

# 测试门禁（改动后必跑）
python selftest.py                 # 根目录，122 项
cd scripts && python -m unittest   # 全量单测
```

写 `data/*.json` 的代码，除了上面的门禁，**再过一遍 3.3 的六条**。
