#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 档：本地待发稿标题去「游戏厅：」栏目前缀（2026-11-06，PRD #79）。

只做两处，都是**确定性定点替换**：不重生成、不改正文、不改换行格式。

1. 游戏仓 `content/*/platform/*.md` 的 h1：`# 游戏厅：X` → `# X`
2. 案例库 `data/toutiao_titles.json` 的 11 条 `title`：`游戏厅：X` → `X`

🚨 明确不在本脚本范围（逐条理由见 PRD #79，别顺手扩）：

- `data/publish_manifest.json` 8 条标题：全是 `published` / `draft_saved` 的
  **线上真实标题留痕**。改了会让「已发表列表按标题命中」和 `db` 导入错位
  （PRD #70：改平台侧已有内容的字段只认公开页回读）。属 A 档「线上旧文另议」。
- `out/articles/ext-ep-*.html` 13 份：2 份 `-b2.html` 是二号认亲源（#72 ③），
  11 份非 `-b2` 是 wechat 产物。A 档不动 HTML。
- `out/toutiao/<cid>/meta.json` 11 份：`build_article` 每次重算会覆盖；
  头条实际标题优先级 `--title > toutiao_titles.json > meta.json > md`。
- 游戏仓 `content/*/meta.json` 的 `title`：是「游戏厅：<裸项目名>」，删前缀
  只剩裸名（撞「标题 = 产品说明」），且是 `bridge.title_for`／一号草稿认亲源
  ⇒ 要改得整条换成技术向标题，属另一轮。
- 游戏仓 `data/titles.json`：已是技术向，0 处栏目。
- `retone_drafts.HTML_TITLES` / `MD_TITLES`：全是 AI case 路径，与 `ext-ep-*` 无关。

用法::

    python scripts/strip_title_prefix.py            # 干跑，只报告
    python scripts/strip_title_prefix.py --go      # 真改（先全量校验，再统一写）
    python scripts/strip_title_prefix.py --check   # 发布前自检：0 残留 exit 0

格式探测（按磁盘字节，不猜）：md 是 LF + 末尾换行；`toutiao_titles.json`
是 **CRLF + 无尾换行**。所以不能用 `json.dumps` 重写整本（会变 LF + 尾换行，
制造 2868 字节的假 diff），只能按字节替换 title 字符串。
"""

import argparse
import copy
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # 案例库根（零绝对路径依赖）
sys.path.insert(0, HERE)

import atomicio                                    # noqa: E402

GAMES_ROOT = os.environ.get("GAMES_ROOT") or os.path.join(
    os.path.dirname(ROOT), "open-source-games")
TITLES_PATH = os.path.join(ROOT, "data", "toutiao_titles.json")

PREFIX = "游戏厅："
H1 = b"# " + PREFIX.encode("utf-8")
PREFIX_B = PREFIX.encode("utf-8")


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- md 平台定稿

def collect_md(root):
    """返回 (patches, variants, body_hits)。

    patch = (path, old_bytes, new_bytes, 新标题, 平台名, 期号)
    只认**首行整行**以 `# 游戏厅：` 开头；拼在别处的变体只报告不动。
    """
    patches, variants, body_hits = [], [], []
    for p in sorted(glob.glob(os.path.join(root, "content", "*",
                                           "platform", "*.md"))):
        with open(p, "rb") as f:
            raw = f.read()
        head, sep, rest = raw.partition(b"\n")
        if b"\r" in head:                          # CRLF 文件不猜，报出来人工看
            variants.append((p, "CRLF 首行"))
            continue
        if raw.startswith(H1):
            new = b"# " + head[len(H1):] + sep + rest
            title = head[len(H1):].decode("utf-8", "replace")
            ep = os.path.basename(os.path.dirname(os.path.dirname(p)))
            plat = os.path.basename(p)[:-3]
            patches.append((p, raw, new, title, plat, ep))
        elif PREFIX_B in head:
            variants.append((p, head.decode("utf-8", "replace")[:60]))
        if PREFIX_B in rest:
            body_hits.append(p)
    return patches, variants, body_hits


def verify_md(patch):
    p, raw, new, title, _plat, _ep = patch
    oh, osep, orest = raw.partition(b"\n")
    nh, nsep, nrest = new.partition(b"\n")
    assert nh == b"# " + oh[len(H1):], (p, "新首行不是原首行去前缀")
    assert nsep == osep and nrest == orest, (p, "首行以外的字节被改动")
    assert len(raw) - len(new) == len(PREFIX_B), (p, "字节差 ≠ 前缀长度")
    assert new.count(b"\n") == raw.count(b"\n"), (p, "行数变了")
    assert title and len(title) >= 12, (p, "去前缀后标题信息量不足")
    return True


# ------------------------------------------------------------ toutiao_titles

def collect_json(path):
    with open(path, "rb") as f:
        raw = f.read()
    obj = json.loads(raw.decode("utf-8"))
    pairs = []
    for cid, ent in obj.items():
        if not isinstance(ent, dict):
            continue
        t = str(ent.get("title") or "")
        if t.startswith(PREFIX):
            pairs.append((cid, t, t[len(PREFIX):]))
    new_raw, expected = raw, copy.deepcopy(obj)
    for cid, old, new in pairs:
        old_b = json.dumps(old, ensure_ascii=False).encode("utf-8")
        new_b = json.dumps(new, ensure_ascii=False).encode("utf-8")
        n = new_raw.count(old_b)
        if n != 1:
            raise SystemExit("✗ %s 的 title 字节串在文件里出现 %d 次（应为 1）"
                             " ⇒ 中止，不写盘" % (cid, n))
        new_raw = new_raw.replace(old_b, new_b, 1)
        expected[cid]["title"] = new
    return raw, new_raw, pairs, obj, expected


def verify_json(raw, new_raw, pairs, expected):
    assert json.loads(new_raw.decode("utf-8")) == expected, "JSON 内容漂移（不止改了 title）"
    assert new_raw.count(b"\r\n") == raw.count(b"\r\n"), "CRLF 数变了"
    assert new_raw.endswith(b"\n") == raw.endswith(b"\n"), "末尾换行状态变了"
    assert len(raw) - len(new_raw) == len(PREFIX_B) * len(pairs), "字节差 ≠ n×前缀"
    for cid, _old, new in pairs:
        assert new and len(new) >= 12, (cid, "去前缀后标题信息量不足")
    return True


# ------------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--go", action="store_true", help="真改（缺省只报告）")
    ap.add_argument("--check", action="store_true",
                    help="只统计残留：0 残留 exit 0，有残留 exit 1")
    ap.add_argument("--games-root", default=GAMES_ROOT, help="游戏仓根（跨仓唯一入口）")
    ap.add_argument("--titles-path", default=TITLES_PATH, help="头条标题表路径")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.titles_path):
        raise SystemExit("✗ 找不到标题表：%s" % args.titles_path)
    if not os.path.isdir(os.path.join(args.games_root, "content")):
        raise SystemExit("✗ 找不到游戏仓 content/：%s" % args.games_root)

    md, variants, body_hits = collect_md(args.games_root)
    raw, new_raw, pairs, _obj, expected = collect_json(args.titles_path)
    n_md, n_json = len(md), len(pairs)

    if args.check:
        print("自检：md h1 残留 %d 份 ｜ toutiao_titles 残留 %d 条 ｜ "
              "首行变体 %d ｜ 正文含前缀 %d 份"
              % (n_md, n_json, len(variants), len(body_hits)))
        if n_md or n_json or variants:
            for p, raw_b, _n, t, plat, ep in md[:8]:
                print("  [md] %s/%s → %s" % (ep, plat, t))
            for cid, old, new in pairs[:5]:
                print("  [json] %s: %s → %s" % (cid, old, new))
            for p, why in variants[:5]:
                print("  [变体不自动改] %s :: %s" % (p, why))
            return 1
        print("✓ 0 残留（A 档范围内）")
        return 0

    # 干跑 / 真改共用同一份报告
    by_plat = {}
    for _p, _r, _n, _t, plat, _ep in md:
        by_plat[plat] = by_plat.get(plat, 0) + 1
    print("命中：游戏仓 platform/*.md %d 份 ｜ toutiao_titles.json %d 条"
          % (n_md, n_json))
    print("按平台：%s" % "  ".join("%s=%d" % (k, by_plat[k])
                                   for k in sorted(by_plat)))
    print("首行变体（不动）：%d ｜ 正文含前缀（不动，A 档只管 h1）：%d"
          % (len(variants), len(body_hits)))
    for p, why in variants[:5]:
        print("  [变体] %s :: %s" % (p, why))
    print("\n样例（去前缀后）：")
    for _p, _r, _n, t, plat, ep in md[:6]:
        print("  %-38s %-14s %s" % (ep, plat, t))
    for cid, _old, new in pairs[:3]:
        print("  %-38s %-14s %s" % (cid, "toutiao_titles", new))
    if n_md == 0 and n_json == 0:
        print("\n✓ 无改动（幂等）")
        return 0
    if not args.go:
        print("\n（干跑）将改 %d 份 md + %d 条标题；加 --go 执行"
              % (n_md, n_json))
        return 0

    # ⚠ 全部校验通过才允许写盘；任一 assert 抛出 ⇒ 一份都不写
    for patch in md:
        verify_md(patch)
    verify_json(raw, new_raw, pairs, expected)

    for p, _r, new, _t, _plat, _ep in md:
        atomicio.write_bytes(p, new)
    if n_json:
        with atomicio.file_lock(args.titles_path + ".lock"):
            # 锁内重读一次，防止并发写覆盖别人的改动
            cur = _read_bytes(args.titles_path)
            if cur != raw and cur != new_raw:
                raise SystemExit("✗ 标题表在校验后被别的进程改过 ⇒ 中止，不写盘")
            if cur == raw:
                atomicio.write_bytes(args.titles_path, new_raw)

    # 落盘后逐字回读比对
    for p, _r, new, _t, _plat, _ep in md:
        assert _read_bytes(p) == new, (p, "落盘回读不一致")
    got = _read_bytes(args.titles_path)
    assert json.loads(got.decode("utf-8")) == expected, "标题表落盘回读不一致"
    assert got.count(b"\r\n") == new_raw.count(b"\r\n"), "标题表 CRLF 丢失"
    print("\n✓ 已写盘并逐字回读：%d 份 md + %d 条标题" % (n_md, n_json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
