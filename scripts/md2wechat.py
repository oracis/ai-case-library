#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把独立稿 Markdown 转成可粘贴的公众号 HTML（全内联样式）。

为什么需要：`wechat_publish publish --file` 要的是「公众号-*.html」，而
`build` 子命令只从 `cases.json` 生成 HTML —— 不在库里的独立稿（方法论稿、
复盘稿）没有任何一步能把 .md 变成可发状态，只能人工在后台手排。

三条硬约束（都是公众号的脾气）：
1. **样式必须内联。** 公众号会剥掉 `<style>` 与 class，只认 style 属性，
   所以这里每个标签都带 style，没有一处在 <style> 里。
2. **不产出 `$`。** 公众号部分模板走 KaTeX，行内 `$` 会被吃成公式。
3. **不留下 Markdown 残渣。** 未配对的 `**` / 反引号宁可原样留着让人看见，
   也不做跨段落的贪婪替换（跨段落会把半篇文章吞进一个 <strong>）。

版式对齐 `wechat_publish.build_one` 的 house style（颜色、字号、左边框），
唯一分歧：行内代码用 `<code>` 带浅底，而不是转成「」。理由是这批是技术稿，
文件名与命令行参数用「」包起来会看不出哪部分是字面量。

用法：
    python scripts/md2wechat.py drafts/wechat/x.md
    python scripts/md2wechat.py drafts/wechat/x.md --out out/wechat/公众号-x.html
    python scripts/md2wechat.py drafts/wechat/*.md drafts/newbatch/*/*.md --outdir out/wechat
"""

import argparse
import glob
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 样式口径
FONT = ("-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',"
        "sans-serif")
MONO = "Consolas,Monaco,'Courier New',monospace"

S_SHELL = "font-family:%s;color:#2b2b2b;font-size:16px;line-height:1.85;letter-spacing:.2px;" % FONT
S_H1 = "font-size:22px;font-weight:700;line-height:1.4;margin:0 0 18px;"
S_H2 = ("font-size:17px;font-weight:700;margin:26px 0 12px;padding-left:10px;"
        "border-left:3px solid #e5b567;")
S_H3 = "font-size:16px;font-weight:700;margin:22px 0 10px;color:#1f2328;"
S_P = "font-size:17px;line-height:1.8;color:rgba(0,0,0,0.9);margin:0 0 14px;"
S_QUOTE = ("font-size:17px;line-height:1.8;margin:0 0 18px;padding:12px 14px;"
           "background:#f6f8fa;border-left:3px solid #d0d7de;color:#57606a;")
S_PRE = ("background:#f6f8fa;border:1px solid #eaeef2;border-radius:6px;"
         "padding:14px 16px;margin:0 0 16px;font-size:14px;line-height:1.6;"
         "white-space:pre-wrap;word-break:break-word;font-family:%s;"
         "color:#1f2328;" % MONO)
S_UL = ("font-size:17px;line-height:1.8;color:rgba(0,0,0,0.9);"
        "margin:0 0 14px;padding-left:22px;")
S_LI = "margin:0 0 8px;"
# 注意：这条**不是** %-格式串，所以宽度写成单个 %。早先误写成 100%%，CSS 无
# 效，表格退回内容宽度（实测 4 列表格只占页面 1/3），肉眼像「被缩进过」。
S_TABLE = ("border-collapse:collapse;width:100%;table-layout:fixed;"
           "font-size:15px;line-height:1.6;margin:0 0 18px;")
S_TH = ("border:1px solid #d0d7de;background:#f6f8fa;padding:8px 10px;"
        "font-weight:700;text-align:left;word-break:break-word;")
S_TD = ("border:1px solid #d0d7de;padding:8px 10px;vertical-align:top;"
        "word-break:break-word;")
S_CODE = ("font-family:%s;font-size:15px;background:#f6f8fa;"
          "border:1px solid #eaeef2;border-radius:4px;padding:1px 5px;"
          "color:#0a3069;word-break:break-word;" % MONO)
S_STRONG = "font-weight:700;color:#1f2328;"
S_HR = "border:none;border-top:1px solid #e1e4e8;margin:28px 0 18px;"

CJK = re.compile(r"[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _join_lines(parts):
    """软换行合并：CJK 之间不插空格，其余插一个空格。

    Markdown 里「换行 = 空格」是给拉丁文设计的；中文段落在编辑器里硬折行后
    按空格拼会多出一堆空洞，所以按边界字符判断。
    """
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if not out:
            out = p
        elif CJK.match(out[-1]) and CJK.match(p[0]):
            out += p
        else:
            out += " " + p
    return out


# ---------------------------------------------------------------- 行内
_TOKEN = re.compile(r"\x00(\d+)\x00")


def inline(text):
    """行内语法：先摘出 code span（防被粗体解析吃掉），再转义，再还原。"""
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    text = re.sub(r"`([^`\n]+)`", stash, text)
    text = esc(text)

    # 链接：这批稿没有，但留着防以后加（公众号外链不可点，纯兜底）
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)",
                  r'<a href="\2" style="color:#0969da;">\1</a>', text)
    # 粗体：禁止跨行、禁止包含 `<`（`<` 已被转义成 `&lt;`，所以这里匹配不到
    # 已被转义的标签，等价于原来那条「不许含 <」的约束）
    text = re.sub(r"\*\*((?:(?!\*\*)[^\n])+?)\*\*",
                  r'<strong style="%s">\1</strong>' % S_STRONG, text)
    # 斜体：边界收紧，避免把 `*` 当列表符或公式
    text = re.sub(r"(?<![\*\w])\*(?!\*)([^\*\n]+?)\*(?![\*\w])",
                  r"<em>\1</em>", text)

    def restore(m):
        return '<code style="%s">%s</code>' % (S_CODE, esc(codes[int(m.group(1))]))

    return _TOKEN.sub(restore, text)


# ---------------------------------------------------------------- 块级
def _is_block_start(line):
    s = line.strip()
    if not s:
        return True
    if s.startswith("```") or s.startswith(">"):
        return True
    if re.match(r"^#{1,6}\s+", s):
        return True
    if re.match(r"^([-*+]|\d+[.)])\s+", s):
        return True
    if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
        return True
    if s.startswith("|"):
        return True
    return False


def _table(lines, i):
    """解析一张表，返回 (html, 下一行索引)。"""
    def cells(row):
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        return [c.strip() for c in row.split("|")]

    head = cells(lines[i])
    sep = cells(lines[i + 1])
    aligns = []
    for s in sep:
        s = s.strip()
        if s.startswith(":") and s.endswith(":"):
            aligns.append("center")
        elif s.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    i += 2
    rows = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append(cells(lines[i]))
        i += 1

    def cell(tag, txt, idx):
        al = aligns[idx] if idx < len(aligns) else "left"
        style = (S_TH if tag == "th" else S_TD) + "text-align:%s;" % al
        body = inline(txt) if txt else "&nbsp;"
        return "<%s style=\"%s\">%s</%s>" % (tag, style, body, tag)

    out = ['<table style="%s">' % S_TABLE]
    out.append("<thead><tr>%s</tr></thead>"
               % "".join(cell("th", t, k) for k, t in enumerate(head)))
    if rows:
        out.append("<tbody>")
        for r in rows:
            # 行比表头短时补空，避免表格塌陷
            r = r + [""] * (len(head) - len(r))
            out.append("<tr>%s</tr>"
                       % "".join(cell("td", t, k) for k, t in enumerate(r)))
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out), i


def md_to_html(md):
    """Markdown 正文 → 全内联样式的 HTML 片段（不含外壳）。"""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        # 围栏代码块：内容原样转义，绝不做行内解析
        if s.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1                                   # 跳过收尾围栏
            body = esc("\n".join(buf)).strip("\n")
            out.append('<pre style="%s"><code style="font-family:%s;'
                       'color:#1f2328;background:none;border:none;padding:0;">'
                       "%s</code></pre>" % (S_PRE, MONO, body))
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if m:
            lvl = len(m.group(1))
            style = S_H1 if lvl == 1 else (S_H2 if lvl == 2 else S_H3)
            tag = "h%d" % min(lvl, 3)
            out.append('<%s style="%s">%s</%s>'
                       % (tag, style, inline(m.group(2)), tag))
            i += 1
            continue

        # 分隔线
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            out.append('<hr style="%s">' % S_HR)
            i += 1
            continue

        # 表格（表头 + 分隔行同时存在才算）
        if (s.startswith("|") and i + 1 < n
                and re.match(r"^\|?[\s:\-\|]+\|$", lines[i + 1].strip())):
            html, i = _table(lines, i)
            out.append(html)
            continue

        # 引用
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            out.append('<blockquote style="%s">%s</blockquote>'
                       % (S_QUOTE, "<br>".join(inline(x) for x in buf if x)))
            continue

        # 无序列表
        if re.match(r"^[-*+]\s+", s):
            items = []
            while i < n and re.match(r"^[-*+]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
                # 缩进续行算同一项
                while (i < n and lines[i].startswith((" ", "\t"))
                       and lines[i].strip()
                       and not re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i])):
                    items[-1] = _join_lines([items[-1], lines[i]])
                    i += 1
            out.append('<ul style="%s">%s</ul>'
                       % (S_UL, "".join('<li style="%s">%s</li>'
                                        % (S_LI, inline(t)) for t in items)))
            continue

        # 有序列表
        if re.match(r"^\d+[.)]\s+", s):
            items = []
            while i < n and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+[.)]\s+", "", lines[i].strip()))
                i += 1
                while (i < n and lines[i].startswith((" ", "\t"))
                       and lines[i].strip()
                       and not re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i])):
                    items[-1] = _join_lines([items[-1], lines[i]])
                    i += 1
            out.append('<ol style="%s">%s</ol>'
                       % (S_UL, "".join('<li style="%s">%s</li>'
                                        % (S_LI, inline(t)) for t in items)))
            continue

        # 段落
        buf = []
        while i < n and lines[i].strip() and not _is_block_start(lines[i]):
            buf.append(lines[i])
            i += 1
        out.append('<p style="%s">%s</p>' % (S_P, inline(_join_lines(buf))))

    return "\n".join(out)


# ---------------------------------------------------------------- 外壳
def md_to_document(md, title=None, summary=None, cover=None):
    """加上外壳与发布说明注释。

    注意注释放在容器**外面**：`wechat_publish.extract_article` 取正文的正则是
    「第一个 div/section 的内容」，注释若写在容器内会让正文提前截断。
    """
    lines = md.replace("\r\n", "\n").split("\n")
    if title is None:
        for ln in lines:
            m = re.match(r"^#\s+(.+)$", ln.strip())
            if m:
                title = m.group(1).strip()
                break
        title = title or ""
    head = ["万物解释者 · 独立稿（md2wechat 生成，可直接粘贴）",
            "使用方法：浏览器打开 → 全选复制 → 粘进公众号后台「新建图文」正文区。",
            "标题：" + title]
    if summary:
        head.append("摘要：" + summary)
    if cover:
        head.append("封面：" + os.path.relpath(cover, REPO))
    comment = "<!--\n" + "\n".join(head) + "\n-->"
    body = md_to_html(md)
    return '%s\n<div style="%s">\n%s\n</div>\n' % (comment, S_SHELL, body)


def slug_of(path):
    """产物名：公众号-<平台>-<stem>.html；wechat 目录的稿不加平台前缀。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    plat = os.path.basename(os.path.dirname(os.path.abspath(path)))
    if plat in ("wechat", "drafts", ""):
        return stem
    return "%s-%s" % (plat, stem)


def convert(path, out=None, outdir=None, summary=None):
    md = open(path, encoding="utf-8").read()
    html = md_to_document(md, summary=summary)
    if out is None:
        out = os.path.join(outdir, "公众号-%s.html" % slug_of(path))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return out


def main():
    ap = argparse.ArgumentParser(description="独立稿 Markdown → 公众号 HTML")
    ap.add_argument("files", nargs="+", help="Markdown 文件（可含通配符）")
    ap.add_argument("--out", help="只转一个文件时的产物路径")
    ap.add_argument("--outdir", default=os.path.join(REPO, "out", "wechat"),
                    help="批量产物目录（默认 out/wechat）")
    ap.add_argument("--summary", help="写进发布说明注释的摘要（可选）")
    a = ap.parse_args()

    paths = []
    for pat in a.files:
        hit = glob.glob(pat)
        paths.extend(hit or [pat])
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        raise SystemExit("✗ 找不到：%s" % ", ".join(missing))
    if a.out and len(paths) > 1:
        raise SystemExit("✗ --out 只能配单个输入")

    for p in paths:
        o = convert(p, out=a.out, outdir=a.outdir, summary=a.summary)
        n = len(open(o, encoding="utf-8").read())
        print("✓ %-58s %6d 字符" % (os.path.relpath(o, REPO), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
