# 同一份 Markdown 分发到 4 个技术社区，标题层级为什么只在一处丢失

## 一、背景：一次分发，四种排版

我把同一批技术稿投到四个平台：一家手机端图文订阅平台、掘金、腾讯云开发者社区、阿里云开发者社区，用的是一条自动化的分发链路：本地 Markdown 源稿 → 按平台适配 → 写进各家的编辑器 → 回读校验。

内容一字不差，排版却分成四种。图文订阅那一端是短段落的节奏感，掘金上标题层级完好，腾讯云上 `##` 和 `**` 直接裸露在正文里，阿里云上则是一整片没有小标题的平铺段落，读者要滑很久才能找到下一节。

一开始我判断是平台把样式「吃」了，挨个去找各家的兼容性问题。查完之后发现判断错了——四个平台里只有一个是真吃标签，其余两个的问题出在我自己的源稿上。这篇记录定位过程和修法，涉及三段可复用的代码。

## 二、定位：先分清编辑器的内核

技术社区的编辑器看起来都是「一个框」，内核差别很大，决定了它能接受什么形态的输入。

| 平台 | 编辑器内核 | 接受的输入 |
|---|---|---|
| 阿里云开发者社区 | mditor（`textarea` + 实时预览） | Markdown 源 |
| 腾讯云开发者社区 | tiptap / ProseMirror 富文本 | HTML 片段，不收 Markdown |
| 掘金 | Markdown 编辑器 | Markdown 源 |
| 博客园 | metaWeblog API | HTML 字符串 |

判断内核有一个很快的办法：看有没有等宽的 `textarea`。阿里云发文页里能查到 `textarea.textarea`（可视输入区）和 `textarea.mditor-hidden`，说明输入的是纯文本源，右侧预览区负责渲染；腾讯云正文区则是一个 `div.tiptap.ProseMirror[contenteditable=true]`，没有 `textarea`，它保存的是节点树，不保存源文本。

内核不同，同一个「粘贴」动作的结果就完全不同：Markdown 源编辑器会把 `##` 解析成标题节点，富文本编辑器只会把它当成两个井号字符。

## 三、实测：把同一段样本投进两家

样本只有四行，覆盖了标题、加粗、列表、代码块四种结构：

    ## 二级标题测试

    正文一段，带 **加粗** 和 `行内代码`。

    - 列表项一
    - 列表项二

    ```python
    print(1)
    ```

分别写进两家的编辑器，再数生成的结构标签和裸露的 Markdown 符号：

**阿里云（mditor）**：预览区生成 `H2 × 1`、`STRONG × 1`、`UL × 1`、`LI × 2`、`PRE × 1`、`CODE × 2`，裸露符号 0 个。结构完整。

**腾讯云（ProseMirror）**：生成 `P × 6`，`H2`、`STRONG`、`UL` 全部为 0，正文里残留 `##` 一处、`**` 两处。结构全丢，符号裸露。

结论很清楚：阿里云并没有丢样式，它老老实实按 Markdown 渲染；腾讯云则是把 Markdown 当成普通文字收进去了。至于阿里云那篇为什么会变成平铺段落——去看源稿就明白了，那一版的小标题写的是「它是什么？」「怎么赚钱？」，全是纯文本行，一个 `##` 都没有。没有结构可渲染，任何编辑器都只能给你一堆 `<p>`。

## 四、修法一：按内核分派写入通道

既然内核不同，写入方式就不能只有一种。我在分发层按平台分出三条通道。

**通道 A：Markdown 源编辑器（阿里云 mditor、掘金）**

直接把 Markdown 写进 `textarea`。注意必须走原生 setter 并派发 `input` 事件，否则框架的绑定不会感知到变化，预览区不会刷新：

```python
def fill_markdown_source(cdp, md):
    """写 Markdown 源编辑器：原生 setter + input 事件，缺一不可。"""
    js = """
    (() => {
      const t = document.querySelector('textarea.textarea');
      if (!t) return -1;
      const set = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype, 'value').set;
      set.call(t, %s);
      t.dispatchEvent(new Event('input', {bubbles: true}));
      return t.value.length;
    })()
    """
    return cdp.eval(js % json.dumps(md))
```

**通道 B：富文本编辑器（腾讯云 ProseMirror）**

不能喂 Markdown，要先转成 HTML，再直插。`insertText` 只会得到纯文本节点，`insertHTML` 才会生成真正的标题节点：

```python
def fill_rich_editor(cdp, html):
    """写富文本编辑器：必须 insertHTML，insertText 只会得到一堆 <p>。"""
    js = """
    (() => {
      const e = document.querySelector('.ProseMirror');
      if (!e) return -1;
      e.focus();
      document.execCommand('insertHTML', false, %s);
      return e.innerText.length;
    })()
    """
    return cdp.eval(js % json.dumps(html))
```

**通道 C：只收 HTML 的接口（博客园 metaWeblog）**

这类没有编辑器可操作，`newPost` / `editPost` 的 `description` 字段直接收 HTML 字符串。Markdown 必须在本地转完再传，平台端不做任何解析。

## 五、修法二：把结构断言放进发布门禁

排版问题难对付的地方在于「发出去才知道」。所以我把结构检查做成发布前的断言：写入完成后，直接数目标区域里的标题节点，对不上就不许提交。

```python
def assert_structure(cdp, scope, expect_h2):
    """提交前自检：标题节点数必须达标，且不得残留 Markdown 符号。"""
    js = """
    (() => {
      const root = document.querySelector('%s');
      if (!root) return {err: 'root-not-found'};
      const t = root.innerText || '';
      return {
        h2: root.querySelectorAll('h2, h3').length,
        raw_hash: (t.match(/^#{1,6} /gm) || []).length,
        raw_star: (t.match(/\\*\\*/g) || []).length
      };
    })()
    """
    r = cdp.eval(js % scope)
    if r.get('err') or r['h2'] < expect_h2:
        raise SystemExit('结构断言失败：期望至少 %d 个标题，实际 %d'
                         % (expect_h2, r['h2']))
    if r['raw_hash'] or r['raw_star']:
        raise SystemExit('结构断言失败：正文残留 Markdown 符号 %d/%d'
                         % (r['raw_hash'], r['raw_star']))
    return True
```

注意断言要排除 `pre` / `code` 节点：代码块里本身就会含 `##` 和 `**`（本篇的样本就是例子），不排除会把示例当成残留误报。我写这篇稿时就地触发过一次——正文结构全对，断言却报「残留 1 处 `##`、4 处 `**`」，排查十分钟才发现命中的是代码块里的示例。

这套断言抓出过我两次事故：一次是富文本通道错用了 `insertText`，标题数恒为 0；一次是源稿漏写小节标记，标题数虽然过线，但正文里的字号全一样。后者说明断言只能证明「有结构」，证不了「结构对」——所以我又加了一条字数下限，防止有人为了过断言硬凑标题。

## 六、两条踩过的坑

**坑一：图文版那套「伪小标题」是个不小的隐患。**

图文版为了手机上读着顺，把每个小节压成一句一段，「它是什么？」这种问句单独一行当小标题用。这套排版在手机上有效，在技术社区就是灾难：同一份稿子渲染出来是上百个 `<p>`，没有任何标题层级，读者无法跳读，平台侧也可能判成「排版混乱」。

结论是图文稿不能当母版。分发链路里应该维护一份**带完整 Markdown 结构的母稿**，给图文平台的那一版由母稿降级生成，而不是反过来。

**坑二：同步工具只保证「内容到达」，不保证「结构到达」。**

我起初用同步助手把 Markdown 一次性推到各家草稿箱，返回都是成功。但「成功」只意味着文字进去了，结构有没有进去取决于目标平台的适配器：源编辑器会解析，富文本适配器可能整段塞成纯文本。所以判据不能看接口的成功码，必须回读目标页面的节点计数。

这两条合起来是一个更一般的教训：**跨平台分发要校验的是「读者看到的东西」，不是「我发出去的东西」。**

## 七、延伸：如果把这套分发搬到云上

目前的链路跑在本地，靠一台常驻浏览器和一个状态文件。如果要多人共用，一个可行的形态是：稿件与素材放对象存储（OSS），分发任务由函数计算（FC）定时触发，幂等键、平台状态、回读结果落数据库。

设计上有三个点必须带着走：一是幂等键（同一条稿 + 同一个平台只允许一次写入），二是失败可重放（平台侧状态未知时先回读再决定，不做盲目重试），三是结构断言作为门禁而不是事后检查。这套东西的难点不在调度，在于「平台没有幂等语义，得自己在本地建账」。

以上是方案设想，尚未实测；本文的实测数据只覆盖前六节的编辑器对照。

## 八、小结

「样式丢了」听起来像平台 bug，实际拆开是两件不同的事：源稿有没有结构，目标内核吃不吃这种结构。前者靠母稿规范解决，后者靠按内核分派写入通道解决，两者都可以用节点计数验证。

排查顺序也值得记一下：先数目标页面的标题节点，再判断是编辑器不收还是源稿没给。跳过这一步直接猜平台兼容性，会像我一样先怀疑错方向。

## 参考资料

- 阿里云开发者社区《博文规范及指引》：https://developer.aliyun.com/article/1639804
- 阿里云开发者社区《博文发布操作和规则说明》：https://developer.aliyun.com/article/762850
- 《如何写好一篇技术分享文章》：https://yq.aliyun.com/articles/1218959
