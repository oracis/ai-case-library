# 掘金已删文章存档（2026-11-02）

2026-11-02 15:20 前后，掘金渠道正式停用并把账号上**全部已发布文章删除**（25 篇）。

## 为什么删

不是清理低质内容，是渠道停用后的收尾。该账号 25 篇里只有 5 篇过审在线，
通过率 20%，且被拒与通过的差异无法从内容侧归因（模板标题、八节技术拆解、
第一人称踩坑体例三种写法都试过）。判定为「平台侧没有客观标准」，
不再投入，账号上的内容一并撤下。

## 这份存档里有什么

- `manifest_all.json` —— 25 篇的完整元数据：`article_id`、标题、发布时间、
  删除时的 `audit_status`、阅读/点赞数、`draft_id`、是否 `deleted`。
  **这是唯一还记录了「曾经发过什么」的地方**，掘金侧已不可查。
- `<article_id>.html` —— 删除前**仍在线**的 9 篇文章的公开页快照。
  剩下 16 篇在删除前已是 404 死链，抓不到页面，但正文在仓库里都有备份：
  - 《游戏厅》系列 → `out/articles/ext-ep-*.html`
  - 案例技术稿 → `out/articles/<case>.md`、`drafts/newbatch/juejin/*.md`

## 怎么读

```bash
python -c "
import json
rows = json.load(open('data/juejin_retired_2026-11-02/manifest_all.json', encoding='utf-8'))
for r in rows:
    print(r['article_id'], r['audit_status'], r['view'], r['title'])
"
```

## 注意

- 掘金的 `article/delete` 是**不可逆**的，没有回收站接口。这份存档是唯一的
  恢复来源。
- `article_id` 参数必须是**带引号的数字字符串**（服务端字段是 `int64` + `,string`
  tag）。传裸数字会让 25 篇全部报
  `json: invalid use of ,string struct tag`，看起来像没有权限，其实只是类型错。
- 已发布文章的列表接口每次**只返回 10 条**，`cursor` 翻页无效；必须删完一批
  才能看到下一批。所以批量删除要写成「拉一页 → 删一页」的循环，不能拉全量再删。
