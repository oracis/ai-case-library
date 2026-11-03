# 交互式编程课程的运行时设计：代码执行沙箱、事件溯源进度与内容权限门控

> 平台：tencentcloud ｜ 案例：Codédex（cod-dex）｜ 状态：本地稿，未推送

在线编程教学的产品形态，本质上是「教程 + 可执行的编辑器 + 判定」。这三件事分开做都不难，难的是让它们在同一个页面里稳定跑起来：用户改一行代码就要立刻看到结果，判定要么通过要么给出可读的报错，进度还要跨设备同步。Codédex 把课程组织成「冒险旅程」，覆盖 HTML/CSS、JavaScript、Python、React 等，每节课配即时练习与勋章。本文只讨论这类系统的通用工程实现，不涉及该产品的内部代码。

## 执行：前端沙箱与后端容器的取舍

代码在哪里跑，决定了整套架构的成本与能力边界。

**前端执行**适合少量语言：JavaScript 直接在 `Web Worker` 或沙箱 `iframe` 里跑，HTML/CSS 用 `srcdoc` 渲染。优点是零延迟、零服务端成本，缺点是只能跑 JS 生态，且必须做隔离——用户代码可以无限循环、可以访问 `window.parent`。

```html
<!-- 用 sandbox 属性剥掉同源能力：没有 allow-same-origin，就拿不到父页面 -->
<iframe id="run" sandbox="allow-scripts" srcdoc=""></iframe>
<script>
  const frame = document.getElementById("run");
  frame.srcdoc = `<script>${userCode}<\/script>`;
  // 父页只通过 postMessage 收结果，不直接读 iframe 内部
  window.addEventListener("message", (e) => {
    if (e.source !== frame.contentWindow) return;
    render(e.data);
  });
</script>
```

`allow-scripts` 而不给 `allow-same-origin`，是关键：两者同时给，沙箱等于没有。代价是用户代码拿不到 `localStorage`、不能 `fetch` 同源接口，需要在文档里说清楚。

**后端执行**覆盖 Python、Java 这类语言：每次提交起一个短生命周期容器，挂只读的代码卷，限制 CPU 与内存，超时强杀。这里最容易出事的是并发与冷启动——容器池要预热，否则用户点「运行」等三秒；资源限制要硬，否则一段 `while True` 能把宿主拖垮。

通用取舍是**双轨**：JS 系走前端，其余走后端。前端负责「即时反馈」，后端负责「能力完整」。

## 判定：不要只比字符串

最朴素的判定是比对 stdout，但它对空格、换行、浮点精度极其敏感，用户明明做对了却被判错，是最伤的体验。

实用的三层判定：

1. **规范化后比对**：去行尾空白、统一换行符、浮点按容差比较。
2. **断言式测试**：给输入喂进去，检查返回值或关键输出是否符合预期，而不是全文相等。
3. **静态检查**：用 AST 检查是否用了指定的语法结构（比如这一课要练 `map`，就检查 AST 里有没有 `.map(` 调用），避免用户用 `for` 循环绕过教学目标。

```python
import ast

def used_call(src: str, name: str) -> bool:
    tree = ast.parse(src)
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == name
        for n in ast.walk(tree)
    )
```

判定结果要结构化返回：通过 / 失败原因 / 期望值 / 实际值。前端按这个结构渲染，而不是把一段自由文本塞进弹窗。

## 进度：用事件溯源，别用「当前状态」字段

学习进度看着简单——第几课、做没做完。但真实场景里它会并发写：用户在两个设备上各做一课，或者一节课里连点两次提交。

如果进度是一行 `progress` 表、每次提交覆盖写，就会出现丢更新。更稳的做法是**事件溯源**：每次完成动作追加一条不可变事件，进度由事件流派生。

```sql
CREATE TABLE progress_event (
  id          bigserial PRIMARY KEY,
  user_id     bigint NOT NULL,
  lesson_id   text   NOT NULL,
  kind        text   NOT NULL,      -- lesson_completed / quiz_passed
  occurred_at timestamptz NOT NULL DEFAULT now(),
  idem_key    text UNIQUE          -- 幂等键，重试不会重复记
);
```

派生逻辑放在读取时或异步物化：`SELECT DISTINCT lesson_id FROM progress_event WHERE user_id = $1`。勋章同理——它是「某组事件同时满足」的规则，规则变化时可以重放历史事件重算，而不是写死一个 `badge_awarded` 布尔值。

这条路线的额外好处是可审计：用户说「我明明做完了」，你能拿出事件时间线，而不是对着一个布尔值各说各话。

## 内容：把课件与运行时解耦

课程内容很容易长成「一节课一个定制页面」，几十节课之后就没法维护。可维护的结构是把内容抽成配置，运行时统一渲染：

```json
{
  "id": "js-04-arrays",
  "runtime": "browser-js",
  "starter": "const xs = [1,2,3];\n",
  "checks": [
    {"type": "stdout", "expect": "[2,4,6]"},
    {"type": "ast", "call": "map"}
  ],
  "reward": {"xp": 40, "badge": null}
}
```

课件是数据，`runtime` 指向执行器，`checks` 是判定规则。新增一节课只写配置，不改代码。这样内容团队和工程团队可以并行，也让多语言课程共用同一套前端壳。

## 权限：门控必须在服务端

免费层与订阅层的边界，最后一定落在「这一课的内容能不能拿到」。常见错误是前端把完整内容都下发、再用 CSS 藏起来——用户打开开发者工具就绕过了。

正确的做法是服务端渲染或服务端裁剪：免费课的完整内容直接返回，付费课只返回摘要与试看片段，激活订阅后再请求完整内容。接口层用统一的策略函数判定，避免每个接口各写一遍：

```ts
async function canRead(userId: string, lessonId: string) {
  const lesson = await getLesson(lessonId);
  if (lesson.tier === "free") return true;
  const sub = await getActiveSubscription(userId);
  return Boolean(sub && sub.status === "active");
}
```

年付与月付在权限层不应有分支——都是 `active` 订阅，只是到期时间不同。把计费周期和访问权限解耦，退款与降级才不会写出一堆特例。

## 成本：内容静态化，执行突发化

这类产品的成本结构与内容平台不同。课程内容是静态产物，走 CDN 后边际成本接近零；真正的变动成本是代码执行——它天然是突发的，用户集中在晚间打开，容器要按峰值准备。

因此工程上有两条硬约束：一是把静态内容与动态执行彻底分离，内容侧无脑 CDN，执行侧单独扩容；二是执行超时与并发上限必须显式设定，否则一节「死循环练习」就能把整池容器占满。订阅制的毛利能不能站住，主要由这两条决定。

## 小结

在线编程课的技术难点不在课程内容，而在四处工程：执行沙箱的隔离强度、判定的可读性、进度的并发正确性、内容权限的服务端门控。这四处做扎实，内容团队才能放心地快速上新，而不必每加一课就找工程改一次代码。

数据来源：产品官网公开信息、公开支付验证页。
