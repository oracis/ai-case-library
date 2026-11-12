#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外链卫生：发布前拦住「自家站点导流 / 索引站口气」（2026-11-02）。

## 为什么有这个文件

11-02 22:26 头条判「包含营销推广类信息」，**禁言 30 天**（不是 7 天，从未在 11-09
到期），点名 2 篇游戏厅稿。诱因是 2 小时连发 11 篇同栏目同结构稿 ＋ 正文给自家
索引站导流（`https://games.ydtgo.top`）。当时只在
`toutiao_publish.strip_source_links()` 里做了剥离，**其他平台没有闸**——公众号、
B站、小红书只要从 `out/articles/*.html` 取稿，就会把同样的段落原样搬过去。

用户 11-02 拍板：**不再推广个人网站，只介绍内容本身。**

所以这里做成一个独立模块，两个用途：

1. `scan()` —— 只读体检：把一段文本/一个文件里所有「自家人域名 + 索引站口气」
   逐条列出来（供人工确认，不修改任何东西）。
2. `assert_clean()` —— 发布前的硬闸：命中就抛 SystemExit，把稿子拦在发送之前。
   临时放行（例如必须引用自家域名做说明）用 `ALLOW_SELF_LINK=1`。

`python scripts/link_hygiene.py` 跑一遍全仓扫描（out/ + data/ 里的文本产物）。

## 判据边界（别误伤）

- **只认自家域名**：`*.ydtgo.top`。第三方站点（GitHub、产品官网）一律不管，
  那是「介绍内容」必需的信息。
- 「索引站口气」只认这几种自我指涉：本站 / 索引站 / 完整索引 / 站点详情 /
  档案编号 / 开源游戏源码索引。产品官网写「官网 www.xxx.app」不算。
"""
import argparse
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 自家域名（含子域）。这里列的是**站点**，不是资源 CDN；封面图那种必要资源
# 单独用 `ALLOW_ASSET_HOST` 跳过（见 scan 的 asset_ok 参数）。
SELF_HOST_RE = re.compile(r"https?://([a-z0-9-]+\.)*ydtgo\.top", re.I)
SELF_HOST_BARE_RE = re.compile(r"(?<![a-z0-9.-])([a-z0-9-]+\.)*ydtgo\.top", re.I)

# 索引站口气：对读者没信息、对平台是「导流意图」的自我指涉
PROMO_PAT = [
    re.compile(r"本站"),
    re.compile(r"索引站"),
    re.compile(r"完整索引"),
    re.compile(r"站点详情"),
    re.compile(r"档案编号"),
    re.compile(r"开源游戏源码索引"),
    re.compile(r"见文末站点"),
]

# 引流话术（2026-11-03 补）：头条的处罚理由是「包含营销推广类信息或存在相关
# 行为」——不只是外链，**把人往站外/社群里拉的话术同样算**。这两句原本写在
# `make_article.py:OUTRO` 里，B 站有剥离、头条没有，于是每篇头条 AI 稿末尾都
# 挂着「完整核实记录在案例库里」「我每天筛一批发在社群里」。
#
# ⚠ 口径要精确到句式，不要用「社群」「案例」这类裸词：正文正常描述产品有
# Discord 社群、有案例研究，不该被拦。
LEADOUT_PAT = [
    re.compile(r"完整核实记录"),
    re.compile(r"在案例库里"),
    re.compile(r"案例库[里中]"),
    re.compile(r"我每天筛"),
    re.compile(r"每天筛一批"),
    re.compile(r"发在社群里"),
    re.compile(r"关注公众号"),
    re.compile(r"公众号[：:]\s*\S+"),
    re.compile(r"加我?微信"),
    re.compile(r"微信号[：:]"),
    # ⚠ 「二维码」不能单独算：David AI 那类案例的商业模式就是「给小店寄带
    #   二维码的明信片」，删掉这句等于删掉正文主体（2026-11-03 试跑撞到，
    #   4 份 getdavid 稿的开头被误删）。只认「让读者去扫」的动作句。
    re.compile(r"扫(一)?扫(?![了过])"),
    re.compile(r"扫码(关注|添加|领取|进群|进入|咨询|了解)"),
    re.compile(r"长按识别"),
    re.compile(r"私信我"),
    re.compile(r"后台私信"),
    re.compile(r"点击领取"),
    re.compile(r"限时免费领"),
]

# 封面/图片这类资源地址：允许（否则每篇都会误报）
ASSET_RE = re.compile(r"(/covers?/|\.png|\.jpg|\.jpeg|\.webp|\.gif)", re.I)


def scan(text, asset_ok=True, leadout=True):
    """返回 [(kind, snippet), …]。kind ∈ self_host / promo / leadout。

    leadout=False 时只查域名与索引站口气（给「正文里确实要提社群」的场景留口子）。
    """
    out = []
    text = text or ""
    for m in SELF_HOST_RE.finditer(text):
        # 域名后面可能跟路径（/covers/x.png）。只看 host 会把封面资源误判成导流，
        # 所以往后再看 120 字判断是不是资源地址。
        if asset_ok and ASSET_RE.search(text[m.start():m.start() + 120]):
            continue
        a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
        out.append(("self_host", text[a:b].replace("\n", " ")))
    for pat in PROMO_PAT:
        for m in pat.finditer(text):
            a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
            out.append(("promo", text[a:b].replace("\n", " ")))
    if leadout:
        for pat in LEADOUT_PAT:
            for m in pat.finditer(text):
                a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
                out.append(("leadout", text[a:b].replace("\n", " ")))
    return out


def assert_clean(text, where="正文", asset_ok=True):
    """发布前硬闸。命中即拦，返回命中的条数（0 = 干净）。"""
    if os.environ.get("ALLOW_SELF_LINK"):
        return 0
    hits = scan(text, asset_ok=asset_ok)
    if not hits:
        return 0
    _KIND = {"self_host": "站外域名", "promo": "索引站口气", "leadout": "引流话术"}
    lines = ["✗ %s 含营销推广类信息，已拦下（%d 处）：" % (where, len(hits))]
    for kind, snip in hits[:8]:
        lines.append("   · [%s] …%s…" % (_KIND.get(kind, kind), snip.strip()[:110]))
    lines.append("  · 自动清理：python scripts/link_hygiene.py --strip <文件或目录>")
    lines.append("  · 游戏稿另可跑 scripts/game_rebrand.py")
    lines.append("  · 确认要放行：ALLOW_SELF_LINK=1 重跑（想清楚再说，"
                 "头条 11-02 22:26 就是按「包含营销推广类信息」禁言 30 天、并下架 2 篇）")
    raise SystemExit("\n".join(lines))


# ---------------------------------------------------------------- 清理
_SENT = re.compile(r"(?<=[。！？!?；;])")
_BLOCK = re.compile(r"(?is)<(p|li|blockquote)\b[^>]*>.*?</\1>")
_TAG = re.compile(r"<[^>]+>")


def _sentence_hit(s):
    """一句话里是否有导流/引流痕迹（不看正文长段落里的正常提及）。"""
    if SELF_HOST_BARE_RE.search(s):
        return True
    if any(p.search(s) for p in PROMO_PAT):
        return True
    return any(p.search(s) for p in LEADOUT_PAT)


def _strip_sentences(txt):
    parts = _SENT.split(txt)
    return "".join(x for x in parts if x and not _sentence_hit(x))


def strip(raw):
    """把营销推广内容删掉，返回 (新文本, [动作说明])。

    两种粒度，按块长短自动选：
      - 短块（<200 字：一条 `<p>` / 一行 md）命中 ⇒ 整块删，这是绝大多数情况，
        「想自己翻？完整索引都在这里：https://…」「完整核实记录在案例库里」都是独立块。
      - 长块命中 ⇒ 只删命中的那几个句子，保住正文主体（避免为了删一句
        而报销一整段）。
    """
    log = []
    is_html = raw.lstrip()[:1] == "<"

    if is_html:
        def repl(m):
            inner = m.group(0)
            plain = _TAG.sub("", inner)
            if not _sentence_hit(plain):
                return inner
            if len(plain.strip()) < 200:
                log.append("删块：%s" % plain.strip()[:46])
                return ""
            body = _strip_sentences(inner)   # 句子边界在标签外侧也成立
            body = re.sub(r"(?is)<p\b[^>]*>\s*</p>", "", body)
            log.append("删句：%s" % plain.strip()[:46])
            return body
        out = _BLOCK.sub(repl, raw)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out, log

    keep = []
    for line in raw.split("\n"):
        if line.strip() and len(line.strip()) < 260 and _sentence_hit(line):
            log.append("删行：%s" % line.strip()[:46])
            continue
        if line.strip():
            line = _strip_sentences(line)
        keep.append(line)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(keep))
    return out, log


# ---------------------------------------------------------------- 全仓扫描
SCAN_DIRS = ["out/articles", "out/toutiao", "out/bili", "out/xhs", "out/wechat"]
SCAN_GLOBS = ["data/*.json"]
TEXT_EXT = (".html", ".md", ".txt", ".json")


def _iter_files(paths=None, with_data=False):
    if paths:
        for p in paths:
            if os.path.isdir(p):
                for f in glob.glob(os.path.join(p, "**", "*"), recursive=True):
                    if f.lower().endswith(TEXT_EXT):
                        yield f
            else:
                yield p
        return
    for d in SCAN_DIRS:
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            for f in glob.glob(os.path.join(p, "**", "*"), recursive=True):
                if f.lower().endswith(TEXT_EXT):
                    yield f
    if with_data:
        for g in SCAN_GLOBS:
            for f in glob.glob(os.path.join(ROOT, g)):
                yield f


def main(argv=None):
    ap = argparse.ArgumentParser(description="营销推广扫描（只读）/ 清理")
    ap.add_argument("paths", nargs="*", help="要扫的文件/目录（缺省扫 out/ + data/）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--data", action="store_true",
                    help="连带扫 data/*.json（台账里的备注不可发布，默认不扫）")
    ap.add_argument("--strip", action="store_true",
                    help="就地删掉命中的营销推广内容（短块整删、长块删句）")
    ap.add_argument("--dry", action="store_true", help="配合 --strip：只看动作不落盘")
    args = ap.parse_args(argv)

    if args.strip:
        if not args.paths:
            raise SystemExit("--strip 必须指定路径，别拿它扫全仓")
        changed = total = 0
        for f in _iter_files(args.paths):
            try:
                with open(f, encoding="utf-8") as fh:
                    s = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if not scan(s):
                continue
            new, log = strip(s)
            if new == s:
                continue
            changed += 1
            total += len(log)
            print("\n%s（%d 处）" % (os.path.relpath(f, ROOT), len(log)))
            for x in log[:5]:
                print("    " + x)
            if not args.dry:
                with open(f, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(new)
        print("\n%s %d 份 / %d 处%s"
              % ("将清理" if args.dry else "已清理", changed, total,
                 "（dry，未落盘）" if args.dry else ""))
        return 0

    rows, total = [], 0
    for f in _iter_files(args.paths or None, with_data=args.data):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                s = fh.read()
        except OSError:
            continue
        hits = scan(s)
        if not hits:
            continue
        total += len(hits)
        rows.append((os.path.relpath(f, ROOT), len(hits),
                     sorted({k for k, _ in hits})))

    if args.json:
        print(json.dumps([{"file": f, "hits": n, "kinds": k}
                          for f, n, k in rows], ensure_ascii=False, indent=1))
        return 0 if not rows else 1

    if not rows:
        print("✓ 干净：没有自家域名，也没有索引站口气。")
        return 0
    print("命中 %d 个文件 / 共 %d 处：" % (len(rows), total))
    for f, n, kinds in sorted(rows, key=lambda x: -x[1]):
        print("  %-58s %3d 处  %s" % (f, n, ",".join(kinds)))
    print("\n游戏稿可用 scripts/game_rebrand.py 统一改；其余手工或按需放宽。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
