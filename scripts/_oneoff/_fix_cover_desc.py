"""给 6 个 cover_desc 回退成英文的案例插入中文 cover_desc（AI 改写自中文 what_it_does）。

只改封面描述句，不动产品名（name）。字节级插入到各自 "id" 行后，保留纯 LF
（open newline='' 防 Windows 把 \n 转成 \r\n）。插完校验 JSON 可解析。
"""
import sys, json

path = "data/cases.json"
raw = open(path, "r", encoding="utf-8").read()

fixes = {
    "wpconvert-ai-convert-ai-sites-to-wordpress":
        "把 AI 生成的网站一键转成可上线的 WordPress 站点",
    "augora-ai":
        "Shopify 商家的服务端广告追踪与营销自动化",
    "vectosolve":
        "把位图一键转成可编辑的 SVG 矢量图",
    "publbee":
        "Amazon KDP 自助出版作者的 AI 工具箱",
    "podawaa":
        "LinkedIn 内容排期发布与增长分析工具",
    "vid-ai":
        "把想法或脚本一键变成能发布的短视频",
}

for cid, desc in fixes.items():
    anchor = '    "id": "%s",\n' % cid
    i = raw.find(anchor)
    if i < 0:
        print("MISS anchor:", cid)
        sys.exit(1)
    after = raw[i + len(anchor): i + len(anchor) + 24]
    if '"cover_desc"' in after:
        print("already has cover_desc, skip:", cid)
        continue
    ins = '    "cover_desc": "%s",\n' % desc
    raw = raw[: i + len(anchor)] + ins + raw[i + len(anchor):]
    print("OK insert:", cid, "->", desc)

# 校验可解析
data = json.loads(raw)
items = data if isinstance(data, list) else data.get("cases", data)
by = {c.get("id"): c for c in items}
for cid, desc in fixes.items():
    assert by[cid].get("cover_desc") == desc, cid
print("校验通过：6 条 cover_desc 均已写入且可解析")

open(path, "w", encoding="utf-8", newline="").write(raw)
print("written (LF preserved)")
