# Next.js SaaS 脚手架的工程化边界：认证、支付回调与事务邮件的模块化取舍

> 平台：tencentcloud ｜ 案例：ShipFast（shipfast）｜ 状态：待确认（本地稿，未推送、未发布）

模板型 SaaS 脚手架的商业特征会反过来塑造技术选择。以 ShipFast 的公开数据为例：一次性买断 $199/$249/$299，累计 $1.27M，近 30 天约 $2,989，约 8,348 名用户，1 人维护，2023 年 9 月上线。这个组合说明收入前置、维护长尾；工程重点不是继续堆功能，而是让认证、支付、邮件这些跨项目复用模块边界清晰、升级成本可控。下面只讨论通用实现，不涉及该产品的内部代码。

## 认证：会话层与业务用户层分离

Next.js App Router 下，认证通常落在三处：`middleware` 做路由级拦截，Route Handler / Server Action 做服务端校验，客户端只读会话状态。常见误区是把 OAuth provider 返回的用户对象直接当业务用户表使用。更稳妥的做法是：provider 身份只负责“证明你是谁”，业务用户表负责“你在系统里是谁”。

通用做法是保留 `accounts` 表存 provider + providerAccountId，业务 `users` 表存内部 userId，会话里只放 `userId` 和可选的 `tenantId`。这样 GitHub / Google 账号合并、邮箱变更、注销都不会污染业务外键。

```ts
// auth.ts 通用示意
export const { handlers, auth } = NextAuth({
  adapter: dbAdapter,
  session: { strategy: "database" }, // 需要服务端撤销会话时优先
  providers: [GitHub, Google],
  callbacks: {
    session({ session, user }) {
      session.user.id = user.id;
      return session;
    },
  },
});
```

`middleware` 只做粗粒度拦截，真正的权限判断放在数据访问层。模板项目尤其要避免把角色判断写进页面组件，否则每次升级路由结构都要重新审计。会话策略上，JWT 省一次数据库读，但撤销和强制下线麻烦；database session 多一次查询，换来可管理性。对 1 人维护的模板项目，后者通常更省心。

## 支付回调：webhook 是唯一事实源

支付集成最容易埋雷的地方，是把 `success_url` 当发货依据。浏览器跳转不可靠，用户可能关页面、网络可能断；Stripe webhook 才是支付状态的事实源。通用要求是：验签、读取原始请求体、幂等处理、按事件类型更新 entitlement，而不是按“付款成功”一个布尔值走天下。

```ts
export async function POST(req: Request) {
  const sig = req.headers.get("stripe-signature")!;
  const raw = await req.text();
  const event = stripe.webhooks.constructEvent(
    raw, sig, process.env.STRIPE_WEBHOOK_SECRET!
  );

  // 事件去重：event.id 唯一索引
  if (!(await markEventProcessed(event.id))) return new Response("ok");

  switch (event.type) {
    case "checkout.session.completed":
      await grantEntitlement(event.data.object);
      break;
    case "charge.refunded":
      await revokeEntitlement(event.data.object);
      break;
  }
  return new Response("ok");
}
```

一次性买断和订阅的差异主要在 entitlement 模型：订阅看周期与状态，买断看 license / seat / 版本权益。退款、争议、手动补单都要能落到同一张权益表。webhook 处理应尽量短，重活丢队列；失败要能重试，重复事件要幂等。模板项目还要提供本地转发与测试事件的最小配置，否则每个买家都要自己踩一遍签名校验。

## 事务邮件：用 outbox 把一致性与投递分开

事务邮件不是营销邮件，它由业务事件触发，比如注册验证、付款回执、邀请成员。常见错误是在数据库事务里直接调邮件 API：邮件超时会拖长事务，邮件失败又可能导致业务回滚。更稳的模式是 outbox：业务写入和待发消息在同一事务落库，独立 worker 负责投递与重试。

```ts
await db.transaction(async (tx) => {
  const order = await tx.insert(orders).values(input).returning();
  await tx.insert(outbox).values({
    type: "email.receipt",
    payload: { orderId: order.id },
    idempotencyKey: `receipt:${order.id}`,
  });
});
```

worker 按 `idempotencyKey` 去重，失败指数退避，超过阈值进死信。模板层应把“模板渲染”和“投递通道”解耦：React Email 只负责生成 HTML，SMTP / API 供应商可替换。发件域要配置 SPF、DKIM、DMARC，退信和投诉要有最小处理路径。对模板型项目来说，邮件模块的接口稳定性比支持多少家供应商更重要。

## 模板型项目的可维护性：模块边界与配置契约

模板项目卖出后，维护者面对的是大量分叉仓库。可维护性的核心是边界：`packages/auth`、`packages/billing`、`packages/email` 各自暴露窄接口，`app/` 只做编排。环境变量用 schema 在启动时校验，避免运行到支付回调才发现缺 key。数据库迁移用 expand-contract，先加列、再双写、最后删旧列，让买家可以分批升级。

从公开数据看，ShipFast 已进入维护期：近 30 天约 $2,989，MRR 显示无活跃订阅，收入占创始人月收入从约 40% 降到约 3.5%。这不是技术问题，但会直接影响技术投入：1 人维护、17 个产品、7,000 订阅读者、55,000 推特粉丝的分发结构，意味着模板收入前置、维护长尾。工程上要承认这个现实，把升级成本做低，而不是承诺无限功能。

## 依赖升级：把升级当产品功能

Next.js 大版本升级通常受 React、Node LTS、构建工具和部署平台共同约束。实用策略是分层锁版本：框架与 React 同组升级，类型依赖单独组，支付和邮件 SDK 按 changelog 小步走。Renovate / Dependabot 要分组并限制并发，避免一次 PR 炸出几十个变更。CI 至少覆盖三类测试：路由冒烟、webhook 契约、邮件模板快照。升级前保留回滚分支，数据库变更与代码发布分离。

对模板项目，升级不是“发个新版本”就结束，还要提供迁移说明和 codemod。买家分叉越深，升级越难；因此核心模块应尽量少改公开接口，把变化收在适配层。一次性买断模式让收入集中在前期，维护期会以技术债的形式出现。边界清晰、配置可校验、升级可回滚，比多打包几个页面更能决定这类脚手架的生命周期。
