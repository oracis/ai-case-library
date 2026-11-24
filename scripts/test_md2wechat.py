# -*- coding: utf-8 -*-
"""md2wechat.py 的回归锁：转换出来的 HTML 必须能直接粘进公众号。

为什么单独测：这一步的失败形态全是「看起来正常」——
  · 样式没内联 → 后台一贴全变黑体，源 HTML 却完全合法；
  · 表格宽度写成 `100%%` → CSS 无效、表格退回内容宽度，肉眼只像「被缩进」；
  · 发布说明注释写进容器内 → `extract_article` 的正文在注释处截断，
    抽出来的 body 少一半，但标题照样对；
  · 代码块里的 `<div>` 没转义 → 粘进后台变成真标签，段落结构当场炸掉。
以上四条都在下面钉死。

用法：python scripts/test_md2wechat.py
"""

import glob
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import md2wechat as M                                              # noqa: E402
import wechat_publish as W                                         # noqa: E402

fails = []
passed = 0


def chk(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  [PASS] %s %s" % (name, extra))
    else:
        fails.append(name)
        print("  [FAIL] %s %s" % (name, extra))


def section(title, fn):
    """单段抛异常也继续跑，别让「跑一半 + 退出码 0」骗人。"""
    try:
        fn()
    except Exception as e:                                         # noqa: BLE001
        fails.append("%s（抛异常）" % title)
        print("  [FAIL] 这一段抛异常中止了：%s: %s" % (type(e).__name__, e))


# ---------------------------------------------------------------------------

def test_inline():
    print("[1] 行内语法")
    h = M.inline("含**粗体**、`code()` 与 *斜体*")
    chk("粗体", '<strong style=' in h and ">粗体</strong>" in h, h[:60])
    chk("行内码走 <code> 且带浅底", "<code style=" in h and "background:#f6f8fa" in h)
    chk("斜体", "<em>斜体</em>" in h)
    chk("不残留星号/反引号", "**" not in h and "`" not in h, h)

    h = M.inline("命令 `--source toolify` 与 `source_kind=discovery`")
    chk("命令行参数不丢字符", "--source toolify" in h and "source_kind=discovery" in h)

    h = M.inline("5 * 3 = 15，单独星号不该变斜体")
    chk("算式里的单星号不被当斜体", "<em>" not in h, h)

    h = M.inline("`<div>` 必须转义")
    chk("行内码里的标签被转义", "&lt;div&gt;" in h and "<div>" not in h)


def test_fence():
    print("[2] 围栏代码块")
    md = "```python\n# 这行不是标题\nx = a < b and c > d  # <span> 原样\n```"
    h = M.md_to_html(md)
    chk("包成 <pre><code>", "<pre style=" in h and "<code style=" in h)
    chk("代码里的 # 不当标题", "<h" not in h, h[:60])
    chk("代码里的 < > 被转义", "&lt;span&gt;" in h and "<span>" not in h)
    chk("代码里的比较符也转义", "a &lt; b and c &gt; d" in h, h)


def test_table():
    print("[3] 表格")
    md = ("| 数据面 | 抓取 | 成功 |\n"
          "|---|---|---|\n"
          "| 热门榜 | 前 60 | 58 |\n"
          "| | **空首列** | 0 |")
    h = M.md_to_html(md)
    chk("用 th 做表头", h.count("<th ") == 3, h.count("<th "))
    chk("两行数据 → 6 个 td", h.count("<td ") == 6, h.count("<td "))
    chk("空单元格补 &nbsp;（否则边框塌掉）", "&nbsp;" in h)
    chk("单元格里的粗体仍生效", "<strong style=" in h)
    chk("宽度必须是 100%（100%% 会让 CSS 失效、表格退回内容宽度）",
        "width:100%" in h and "width:100%%" not in h)
    chk("有 border-collapse", "border-collapse:collapse" in h)


def test_lists_quote():
    print("[4] 列表与引用")
    h = M.md_to_html("- 甲\n- 乙 **强调**")
    chk("无序列表", h.count("<li ") == 2 and "<ul " in h, h[:60])
    h = M.md_to_html("1. 一\n2. 二\n3. 三")
    chk("有序列表", h.count("<li ") == 3 and "<ol " in h)
    h = M.md_to_html("> 数据时点：2026-11-23 抓取")
    chk("引用块", "<blockquote style=" in h and "border-left:3px solid #d0d7de" in h)
    h = M.md_to_html("> 第一行\n> 第二行")
    chk("多行引用用 <br> 保留换行", "<br>" in h)


def test_softwrap():
    print("[5] 软换行（中文不插空格）")
    h = M.md_to_html("这一行是硬折行的\n第二行，应该紧贴。")
    chk("中文之间不加空格", "的\n第二" not in h and "的 第二" not in h, h[20:60])
    chk("拼成一个段落", h.count("<p ") == 1)
    h = M.md_to_html("hello\nworld")
    chk("拉丁文之间仍加一个空格", "hello world" in h, h[20:50])


def test_document():
    print("[6] 外壳与抽取口径")
    md = "# 标题甲\n\n正文第一段。\n\n## 小节\n\n正文第二段。"
    html = M.md_to_document(md, summary="摘要乙")
    chk("注释在容器外（否则正文会被截断）",
        html.index("-->") < html.index('<div style="font-family:'))
    chk("标题写进注释", "标题：标题甲" in html)
    chk("摘要写进注释", "摘要：摘要乙" in html)
    chk("容器带 house style 字体", "PingFang SC" in html)
    chk("h1 只出现一次（重复 h1 会让后台标题取错）", html.count("<h1") == 1)
    chk("没有 <style> 标签（公众号会剥掉）", "<style" not in html)
    chk("不产出 $（模板走 KaTeX 会被吃成公式）", "$" not in html)


def test_real_drafts():
    print("[7] 8 篇真实稿（无残留 Markdown、能被 extract_article 抽取）")
    pats = ["drafts/wechat/tool-site-teardown.md",
            "drafts/newbatch/*/tool-site-teardown.md",
            "drafts/*/tool-site-teardown.md"]
    paths = sorted({os.path.normpath(os.path.join(REPO, p)) for pat in pats
                    for p in glob.glob(pat)})
    chk("找到 8 篇（glob 在 Windows 上给反斜杠，必须 normpath 去重）",
        len(paths) == 8, len(paths))
    import tempfile
    tmp = tempfile.mkdtemp(prefix="md2wechat_")
    for p in paths:
        out = os.path.join(tmp, "公众号-%s.html" % M.slug_of(p))
        M.convert(p, out=out)
        t = open(out, encoding="utf-8").read()
        tag = os.path.relpath(p, REPO).replace("\\", "/").split("drafts/")[1]
        chk("%s 无 ** 残留" % tag, "**" not in t)
        chk("%s 无反引号残留" % tag, "`" not in t)
        chk("%s 无 $ " % tag, "$" not in t)
        ex = W.extract_article(out)
        chk("%s 抽得到标题" % tag, bool(ex["title"]), (ex["title"] or "")[:20])
        # 正文长度要接近整篇：注释若跑进容器，body 会被截掉一半
        chk("%s 正文没被截断" % tag, ex["body_len"] > len(t) * 0.7,
            "%d / %d" % (ex["body_len"], len(t)))
        chk("%s 只有一个 h1" % tag, t.count("<h1") == 1, t.count("<h1"))


def main():
    print("=" * 62)
    print("  md2wechat：Markdown → 公众号 HTML（离线）")
    print("=" * 62)
    print()
    section("行内语法", test_inline)
    section("围栏代码块", test_fence)
    section("表格", test_table)
    section("列表与引用", test_lists_quote)
    section("软换行", test_softwrap)
    section("外壳与抽取口径", test_document)
    section("真实稿批量", test_real_drafts)
    print("=" * 62)
    if fails:
        print("✗ %d 项失败 / 共 %d：" % (len(fails), passed + len(fails)))
        for f in fails:
            print("   ·", f)
        return 1
    print("✓ 全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
