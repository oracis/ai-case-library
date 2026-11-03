# 浏览器端棋类分析引擎的工程取舍：WASM 引擎、PGN 增量解析与对局状态同步

> 平台：tencentcloud ｜ 案例：Chessigma（chessigma）｜ 状态：本地稿，未推送

棋类平台的功能重心，过去十年从「能下棋」挪到了「下完能复盘」。复盘要跑引擎，而引擎是纯算力消耗——这一步放在服务端，用户量一大就是账单；放在浏览器里，算力由用户自己出。Chessigma 的公开定位是「免费、无限次的棋局分析与报告」，同时支持导入 Chess.com、Lichess 的棋谱与本地 PGN。要做到「无限次」，客户端算力几乎是唯一经济可行的路径。下面只讨论这类系统的通用工程问题，不涉及该产品的内部代码。

## 引擎：WASM 是默认选项，难点在隔离与内存

把 Stockfish 这类引擎编译成 WebAssembly 已经是成熟路线，lichess 等站点长期在用。真正的工程细节有三处。

**一是运行位置。** 引擎搜索是 CPU 密集的同步循环，放在主线程会直接卡住界面。标准做法是丢进 Web Worker，主线程只负责发 `position`、`go` 指令和收 `info` 流。

```js
// engine.worker.js —— 主线程与引擎之间只传字符串协议
let sf = null;
onmessage = async (e) => {
  const { cmd, payload } = e.data;
  if (cmd === "init") {
    // 引擎脚本与 NNUE 网络文件分开加载，后者体积大，要单独缓存
    importScripts("/engine/stockfish.js");
    sf = await Stockfish();
    sf.addMessageListener((line) => postMessage({ type: "info", line }));
    sf.postMessage("uci");
  }
  if (cmd === "analyse") {
    sf.postMessage(`position fen ${payload.fen}`);
    sf.postMessage(`go depth ${payload.depth ?? 18}`);
  }
  if (cmd === "stop") sf.postMessage("stop");
};
```

**二是多线程。** 引擎并行搜索依赖 `SharedArrayBuffer`，而浏览器只在**跨源隔离**（cross-origin isolated）的页面里开放它。这要求响应头同时带上两条：

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

代价是页面上所有跨域资源（字体、图片、第三方脚本）都必须显式声明 `Cross-Origin-Resource-Policy` 或走 CORS，否则会被拦。很多站点做完这一步发现「图挂了」，原因就在这里。取舍很直接：单线程能跑但分析慢，多线程要付整站的资源策略改造。

**三是网络权重文件。** 现代引擎用 NNUE 做局面评估，权重文件几十兆。它必须按内容哈希命名、长缓存、可断点续传，并且允许用户在低带宽下降级到纯手工评估特征。加载失败要有明确降级，而不是静默卡在「分析中」。

## 棋谱：PGN 不是纯文本，是带嵌套的语法树

棋谱交换格式基本是 PGN，它看着像文本，实际有结构：对局元数据在 `[Tag "value"]` 头里，着法主体里用 `{}` 放注释、用 `()` 放变例（RAV），而变例可以任意嵌套。用正则切着法会在第一层嵌套处崩掉。

稳妥做法是手写一个小的递归下降解析器，把着法流解析成树，再按需展平。

```ts
type Node = { san: string; comment?: string; variations: Node[][] };

function parseMoves(tokens: string[]): Node[] {
  const line: Node[] = [];
  while (tokens.length) {
    const t = tokens.shift()!;
    if (t === "(") {                       // 变例：递归一层，挂到上一个着法
      const sub = parseMoves(tokens);
      const last = line[line.length - 1];
      if (last) last.variations.push(sub);
      continue;
    }
    if (t === ")") return line;
    if (t.startsWith("{")) {               // 注释紧跟其所在的着法
      const c = t.slice(1, -1);
      const last = line[line.length - 1];
      if (last) last.comment = c;
      continue;
    }
    if (/^\d+\.+$/.test(t)) continue;      // 着法序号，不进树
    line.push({ san: t, variations: [] });
  }
  return line;
}
```

解析完还要解决「局面从哪来」：分析器需要 FEN。FEN 必须由起始局面逐走着法推出来，而不是从棋谱里读——棋谱里的 FEN 头只表示初始局面，中途局面得自己算。这一步要用到完整的走法生成与合法性校验（包括吃过路兵、王车易位权、五十步和三次重复），任何一条漏掉，用户复盘时就会看到「引擎建议了一步非法棋」。

## 对局：服务端权威、时钟对齐、断线幂等

在线对局的技术核心不是同步走法，而是**谁说了算**。客户端只发「我想走 e2e4」，服务端校验合法性、落库、广播；客户端本地先走一步做乐观渲染，被拒时回滚。这样既没有输入延迟，也不会出现两边棋盘不一致。

时钟是另一个高频坑。棋类对局的时间扣减必须由服务端裁决，客户端只显示。为了显示不飘，客户端要维护一个与服务端的偏移估计：

```ts
// 用往返时延的一半校正本地时钟，避免「我这边还剩 3 秒，服务器已经判负」
let offset = 0;
function syncClock() {
  const t0 = Date.now();
  fetch("/api/time").then(r => r.json()).then(({ serverNow }) => {
    const rtt = Date.now() - t0;
    offset = serverNow - (t0 + rtt / 2);
  });
}
const serverTime = () => Date.now() + offset;
```

断线重连要求接口**幂等**：客户端带着 `gameId + lastSeq` 重连，服务端补发缺的事件而不是重发全量。走法提交要带客户端生成的幂等键，避免用户网络抖动重试时走两步。

## 成本：把算力挪到客户端，是「免费」的技术前提

这类产品的成本结构和普通 SaaS 不同。分析在浏览器里跑，服务端只承担信令、棋谱存储和账号，单位用户的边际成本接近于零；真正的固定成本是带宽（传棋谱和权重文件）与静态资源的 CDN 分发。

反过来说，如果分析放在服务端，「无限次免费」在数学上就不成立：一次 18 层搜索在服务端是实打实的 CPU 秒，用户越多亏得越快。这也解释了为什么这类产品普遍把分析做成客户端能力、把服务端能力限制在「同步 + 存档」上。

## 小结

把引擎搬进浏览器，换来的是成本结构而不是功能优势。工程上真正要投的是四处：Worker 隔离与跨源隔离带来的整站资源策略、NNUE 权重的加载与降级、PGN 嵌套语法树的解析、以及服务端权威的对局与时钟。这四件事做扎实，「免费无限分析」才是一个可持续的技术承诺。

数据来源：产品官网公开定位、应用商店公开信息、公开支付验证页。
