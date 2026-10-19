# QQ 龙虾 Bot：主动群消息、限制与模型接入 FAQ

整理日期：2026-10-19。原有合规及历史资料核对于 2026-10-18（北京时间）；本次补充重新核对了腾讯 Agent 接入、消息概述、私聊/群聊发送、私聊流式、群消息撤回及菜单面板页面，并静态检查本插件源码。平台规则可能更新，请以来源最新版本及实际账号权限为准。本页区分官方说明、源码实现、受控实测与历史第三方资料，不构成平台批准或法律意见。

## 适用哪类机器人？

这里的“龙虾 Bot”指 QQ 官方快捷创建入口中扫码创建、绑定 Agent 的机器人，不是个人 QQ 号协议机器人。官方将创建过程描述为扫码创建、复制 key、配置 Claw，并提供 Hermes、OpenClaw 等接入指南。[来源：QQ 官方 Agent 接入文档](https://bot.q.qq.com/wiki/agent-qqbot/)

快捷创建入口：https://q.qq.com/qqbot/openclaw/login.html

## 能否主动向群聊发送消息？

**平台支持，但需要机器人已入群且具备相应群权限。** 官方 Agent FAQ 明确介绍“主动推送消息能力：机器人主动在群聊内发言，如定时任务推送等”，并说明群主可添加机器人及设置相关权限。[来源：官方 Agent FAQ](https://bot.q.qq.com/wiki/agent-qqbot/#常用-faq-指引-持续更新)

接口为 `POST /v2/groups/{group_openid}/messages`。主动发送测试不携带用于被动回复的 `msg_id` 或 `event_id`；成功应核对返回的消息 ID，不能仅凭调用没有抛异常判断送达。[来源：官方群消息接口](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)

### 本地受控实测（2026-10-18）

以下是维护环境中的一次真实账号 REST 接口测试摘要，不是自动化测试，也不是所有账号、插件版本或宿主主动发送路径的兼容性保证：

| 条件 | 结果 |
|---|---|
| 现有凭据获取访问令牌 | HTTP 200 |
| 同一机器人向已有群主动发送纯文本，不带 `msg_id` / `event_id` | HTTP 400，`40034105` |
| 群主报告已开启权限后，对同一机器人与群复测 | HTTP 200，返回有效消息 ID；响应时间为 2026-10-18 23:29:28 +08:00 |

证据来源：当日经用户授权执行的令牌请求和直接群消息 POST 响应；本文仅保留去标识化结果，未附公开原始日志，不能独立重放审计。不包含 AppID、AppSecret、Token、群 OpenID、用户 ID、完整消息 ID 或 trace。

`40034105` 的官方含义是“主动消息发送失败，无权限”，建议检查机器人权限设置。它本身不能证明一定是哪一个开关未开启。[来源：接口错误码表](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)

**具体客户端菜单路径尚未核实，故不提供猜测性的点击教程。** 请群主在最新版 QQ 中检查该群机器人的权限；不要将插件侧 allowlist、群消息全量接收授权和主动推送授权混为一谈。插件配置不能自行赋予 QQ 服务端权限。

## 私聊、群聊的主动与被动消息分别限制多少？

这里的私聊指 **QQ 单聊 C2C**，不是“频道私信”。下列为腾讯公开规则，不是本地压测结果。`qps` 为每秒请求数，`qpm` 为每分钟请求数；各维度限制同时成立。

### 主动消息（不带被动回复的 msg_id / event_id）

| 限制维度 | QQ 私聊 C2C | QQ 群聊 |
|---|---|---|
| Bot 发送方：企业认证 / 个人身份证认证 | 10 条/秒 | 60 条/分钟 |
| Bot 发送方：未认证 | 5 条/秒，且 30 条/分钟 | 30 条/分钟 |
| 单关系接收方 | 20 条/分钟 | 20 条/分钟 |
| 每日接收上限 | 官方原文：“每个好友 1 天最多接收 1000 条” | 官方原文：“每个群 1 天最多接收 1000 条” |
| 权限前提 | 具备好友关系、用户未拒收主动消息，账号有相应权限 | 已入群、群内主动推送权限允许，机器人未被禁言等 |

扫码创建不等于已完成个人或企业认证，不能自行把当前机器人归入认证档。跨用户/跨群发送还需考虑 Bot 维度频控；不推定私聊和群聊 Bot 额度是否共用。公开页面未在此处详述每日额度的重置时区、跨机器人共享口径及剩余额度查询方式，不自行补齐。

用户可在 QQ 客户端关闭“允许主动发送”；关闭后主动发送失败。`40034100` 表示主动消息超过频控；`40034105` 表示主动消息无权限。**不带 msg_id 并不意味着绕过配额或授权。**

### 被动消息（回复用户消息）

| 限制维度 | QQ 私聊 C2C | QQ 群聊 |
|---|---|---|
| 回复有效期 | 60 分钟 | 5 分钟 |
| 每条入站消息最多回复 | 4 次 | 5 次 |
| 回复授权标识 | 入站消息的 `msg_id` | 入站消息的 `msg_id` |
| 去重 | 同一 `msg_id + msg_seq` 重复发送失败；多次回复递增 `msg_seq` | 同左 |

次数是**每条入站消息**的回复次数，不是每分钟额度；递增 `msg_seq` 不能增加回复总次数。官方另允许携带 `event_id` 响应受支持事件；不能据这张针对消息回复的表断言所有事件拥有相同次数与时效。

**官方页面存在一处不一致：**《发送单聊消息》顶部和《消息收发概述》都写“60 分钟、4 次”，但单聊接口参数表 `msg_id` 的描述仍写“5 分钟内有效”。本文采用两处规则说明的 60 分钟口径，并保留此冲突；它不是本次实测确认的时长，最终以服务端执行及官方后续修订为准。本插件当前常量也是 C2C 3600 秒/4 次、群 300 秒/5 次，见 [message_rules.py:13–14](hermes_qqbot/message_rules.py#L13-L14)。过期或用尽时拒绝，不会偷偷改成主动消息。

### 接口总频控、流式及互动召回

- 普通私聊、群聊发送接口各列 **100 QPS**；单聊页面明确包含主动、被动等所有消息类型。这是接口层限频，**不能替代主动消息配额**，也不能据此认为被动回复无限制。
- 私聊原生流式接口另列 **50 QPS**；群聊明确“不支持流式参数”。官方该页未单列分片如何计入所有消息配额，不能把每帧次数自行换算成额外回复额度。
- 私聊 **互动召回**：用户主动对话后未来 30 天，按官方列出的“当天、1–3 天、3–7 天、7–30 天”四个周期，每周期可发 1 条，合计 4 条；使用 `is_wakeup=true`。普通单聊发送接口说明其与 `msg_id` / `event_id` 互斥。它不是无限主动推送，也不是把普通被动窗口延长为 30 天；边界归属以平台执行为准。
- 以上并不免除内容审核、消息长度、媒体格式、账号权限及用户拒收等限制。本插件未实现覆盖所有主动发送路径的统一分布式配额器，QQ 服务端仍为最终裁决方。

来源：[消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)、[发送单聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)、[发送群聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)、[流式发送单聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_stream_messages.post.html)。

## 腾讯官方怎样描述“龙虾 Bot”？有哪些能力？

腾讯《Agent 接入》的“QQ 龙虾机器人是什么”将它描述为基于 QQ 原生生态的“一站式龙虾管理与交互入口”，并明确说它**“本身不自带智能交互功能”**，作用是承接 OpenClaw、Hermes、Workbuddy、QClaw 等 Claw 服务。即消息外壳/通道不是模型本身，图片理解、语音转录、生成图片等还依赖所接模型和工具。

该页列出：扫码创建、复制 key、配置 Claw；手机/电脑使用；文字、语音、图片、视频、文件上下行；单个 QQ 账号可创建 **5 只**独立龙虾 Bot；第三方 Agent 可通过扫码 SDK 获得 AppID/AppSecret。官方 Hermes 章节介绍的是 **Hermes 内置通道的配置方式**，不是对本第三方插件的认证或背书。

Agent FAQ 还明确写明：

- QQ 机器人**进群能力已全面开放**，群主可添加机器人并设置相关权限，需使用最新版 QQ。
- 群主允许后可**接收群内全部成员发言**。
- 可**主动在群聊内发言**，例如定时推送。
- 被群主设为群管理员后，可**撤回成员消息**。撤回接口进一步规定：普通成员机器人只能撤回自己的消息，管理员机器人可撤回自己及普通群成员消息，均受 **2 分钟**时限约束。

开发文档还列有 C2C 原生流式、文本/Markdown/富媒体、输入中状态、引用、键盘按钮与交互事件、互动召回、机器人信息/分享链接、菜单与指令面板，以及频道相关接口等。**文档存在某 API 不等于每个扫码机器人无条件拥有全部权限**，应分别核对场景、认证、授权、客户端版本和实际调用结果；不将频道能力等同于普通 QQ 群能力。

## 本插件实现了多少？

以下以当前源码对照 **16 个选定能力项**：**10 项已有实现（含场景限制），1 项仅显式底层透传，5 项未发现专用实现**。这是便于查阅的本地功能清单，不是腾讯全部 API 的覆盖率，也不等于 10 项全部真实账号验收通过。“未发现”指此次静态检查未见入口/处理流程，不表示平台不支持。禁言、踢人等不在此计数内，不据菜单中出现“群聊管理”就承诺已对所有龙虾账号开放。

| 能力项 | 腾讯官方开放情况 / 来源编号 | 本插件实现与边界 | 源码证据 |
|---|---|---|---|
| 1. 扫码绑定 | Agent 接入与第三方 SDK [S1] | 已实现交互向导、扫码轮询/过期刷新、凭据保存；不在本文宣称重新真验扫码 | [onboard.py:59–127](hermes_qqbot/onboard.py#L59-L127)、[plugin.py:80–111](hermes_qqbot/plugin.py#L80-L111) |
| 2. WebSocket 接收事件 | 开发文档 WebSocket [S8] | 已实现鉴权、心跳、重连及事件分发 | [adapter.py:311–328](hermes_qqbot/adapter.py#L311-L328)、[adapter.py:483–557](hermes_qqbot/adapter.py#L483-L557) |
| 3. 私聊 / 群@文本、Markdown | 普通消息接口 [S3][S4] | 已实现收发与分段；Markdown 受配置/权限影响，频道发送另有边界 | [adapter.py:815–820](hermes_qqbot/adapter.py#L815-L820)、[adapter.py:1394–1457](hermes_qqbot/adapter.py#L1394-L1457)、[adapter.py:1665–1673](hermes_qqbot/adapter.py#L1665-L1673) |
| 4. 图片、语音、视频、文件通道 | Agent 多模态说明、富媒体 [S1][S2] | 已有入站附件/引用解析及 C2C/群媒体发送、URL/本地上传；此发送路径不支持频道媒体；不自带识图/ASR/TTS模型 | [adapter.py:874–906](hermes_qqbot/adapter.py#L874-L906)、[adapter.py:1677–1736](hermes_qqbot/adapter.py#L1677-L1736) |
| 5. C2C 原生流式 | 私聊流式 [S5]，非群聊 | 已实现，需现代兼容宿主及本轮回复来源；旧宿主缓冲最终发送。真实最终帧已有历史验证，多帧渐进显示未完整真验 | [streaming.py:85–170](hermes_qqbot/streaming.py#L85-L170)、[streaming.py:200–255](hermes_qqbot/streaming.py#L200-L255) |
| 6. 私聊 / 群主动发送 | 主动配额与群推送 [S1][S2][S3][S4] | 已实现独立 REST sender、宿主通知接入及 CLI；CLI 默认预览，显式 `--apply` 才发送。定时调度依赖宿主，非每条发送路径自动变主动 | [senders.py:174–212](hermes_qqbot/senders.py#L174-L212)、[senders.py:274–283](hermes_qqbot/senders.py#L274-L283)、[manage.py:118–133](hermes_qqbot/manage.py#L118-L133)、[plugin.py:57–58](hermes_qqbot/plugin.py#L57-L58) |
| 7. 输入中状态 | 单聊 `msg_type=6` / `input_notify` [S3] | 已实现 C2C 状态通知 | [adapter.py:1796](hermes_qqbot/adapter.py#L1796) |
| 8. 消息引用 | `message_reference` / 引用事件字段 [S3][S4] | 已实现入站引用解析及群视觉引用；没有 REFIDX 时不能伪造，视觉引用不代替被动授权 | [references.py:4–16](hermes_qqbot/references.py#L4-L16)、[adapter.py:1438–1442](hermes_qqbot/adapter.py#L1438-L1442)、[adapter.py:1531–1537](hermes_qqbot/adapter.py#L1531-L1537) |
| 9. 按钮与交互回调 | keyboard / 交互事件 [S3][S4] | 已实现键盘、审批/澄清按钮、回调 ACK 与操作者校验；不等于所有按钮类型均客户端真验 | [adapter.py:605–733](hermes_qqbot/adapter.py#L605-L733)、[adapter.py:1591–1613](hermes_qqbot/adapter.py#L1591-L1613)、[keyboards.py:146–169](hermes_qqbot/keyboards.py#L146-L169) |
| 10. 自定义菜单 / 指令面板 | C2C 菜单、四场景面板 [S7] | 已实现薄 REST 管理客户端及显式 CLI；读/写菜单，面板增删改查/目标关联。不会启动即发布，未证实真实发布效果 | [management.py:27–90](hermes_qqbot/management.py#L27-L90)、[manage.py:28–44](hermes_qqbot/manage.py#L28-L44)、[manage.py:96–107](hermes_qqbot/manage.py#L96-L107) |
| 11. 私聊互动召回 | `is_wakeup`、四周期 [S3] | **仅显式 metadata 透传**；已做与 msg_id/event_id 互斥及仅 C2C 校验，未发现自动召回时机/用户选择/周期配额调度 | [adapter.py:1400–1414](hermes_qqbot/adapter.py#L1400-L1414)、[adapter.py:1571–1587](hermes_qqbot/adapter.py#L1571-L1587) |
| 12. 群全量消息 | 群主授权后开放 [S1][S2] | **未发现** `GROUP_MESSAGE_CREATE` 处理；目前只注册 `GROUP_AT_MESSAGE_CREATE`，不是全量群消息接入 | [adapter.py:815–820](hermes_qqbot/adapter.py#L815-L820) |
| 13. 撤回自己的消息 | 2 分钟内撤回 [S2][S6] | **未发现**消息 DELETE 封装/工具；通用 HTTP 方法存在不算功能实现 | [adapter.py:1517–1540](hermes_qqbot/adapter.py#L1517-L1540)（现有发送路径） |
| 14. 管理员撤回普通群成员消息 | 管理员权限及 2 分钟时限 [S1][S6] | **未发现**成员撤回入口及相应权限流程 | [management.py:27–90](hermes_qqbot/management.py#L27-L90)（现有管理客户端仅菜单/面板） |
| 15. Webhook 入站 | 官方提供回调方式 [S9] | **未发现**接收端、签名验证或 HTTP 回调路由；现有入站为 WebSocket | [adapter.py:518–542](hermes_qqbot/adapter.py#L518-L542) |
| 16. 机器人资料 / 分享链接专用管理 | 官方开发文档列获取详情、生成分享链接等 [S7]导航 | **未发现**对应专用 CLI / 管理客户端，不把通用 `_api_request` 算实现 | [manage.py:28–44](hermes_qqbot/manage.py#L28-L44)、[management.py:27–90](hermes_qqbot/management.py#L27-L90) |

**验证强度须分开看：**上文主动群消息 HTTP 200 是经授权的直接 REST 实测，不是所有宿主定时/插件路径验收。既有插件 smoke 记录包含真实私聊最终帧 ACK 与操作者确认；既有另一条群探针日志仅到进入出站、无明确最终 ACK。自动化测试主要证明代码行为/请求形状，不能证明腾讯账号权限、所有媒体/按钮渲染、全量群消息或菜单已发布。完整边界见 [VERIFICATION.md](VERIFICATION.md)。本次只补文档、静态对照，不重跑真实消息或菜单发布。

### 腾讯官方文档来源索引

| 编号 | 腾讯官方页面 | 本文引用用途 |
|---|---|---|
| S1 | [Agent 接入](https://bot.q.qq.com/wiki/agent-qqbot/) | 龙虾 Bot 定义、5只机器人、扫码、Hermes接入、第三方 SDK、群权限 FAQ、安全须知；其中的 OpenClaw 官方插件不等于本插件 |
| S2 | [消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html) | C2C/群/频道区别、主动/被动/召回定义、配额、媒体及撤回概述 |
| S3 | [发送单聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html) | 私聊配额、100 QPS、四周期召回、输入状态、键盘、引用及错误码；保留60分钟/5分钟冲突说明 |
| S4 | [发送群聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html) | 群配额、100 QPS、不支持流式、键盘/引用及错误码 |
| S5 | [流式发送单聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_stream_messages.post.html) | 50 QPS、分片序号、流ID、生成/结束状态 |
| S6 | [撤回群聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages_message_id.delete.html) | 普通/管理员机器人的撤回范围与2分钟时限 |
| S7 | [自定义菜单与指令面板](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/menu-panel/) | C2C全局菜单、四种场景面板、面板目标关联；侧栏另列机器人详情/分享链接 |
| S8 | [WebSocket 方式](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/websocket.html) | WebSocket事件接入参考 |
| S9 | [Webhook 方式](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/event-emit/webhook.html) | Webhook事件接入参考 |

S1 是**龙虾专项产品说明**，S2–S9 是 **QQ Bot 开发协议说明**，两者应结合阅读，而不能拿历史第三方普通机器人教程代替当前龙虾专项规则。

## 接入 GPT / ChatGPT 是否违规？

不能仅凭“模型能调用”或“群主已授权推送”判断合规。至少分别核对 QQ 规则、模型提供方条款、服务地区及适用法律要求。

### QQ 平台与用户数据

本次查阅的通用运营规范未发现点名禁止 OpenAI/GPT 的条款，**但这不等于官方明确批准，也不代表已核对全部专项规则或后台审核要求**。官方规范要求内容过滤、必要资质，以及用户数据向第三方提供前的许可和用途披露等。[来源：QQ 运营规范，5.1、5.10、8、11 节](https://bot.q.qq.com/wiki/business/)

群主开启主动推送，不等于每位群成员同意将聊天记录交给第三方模型。接收全量群消息也不应被理解为无限制外传许可。公开提供服务、收费运营与个人受控测试的适用要求需分别核对，不能将个人使用描述为当然豁免。

### OpenAI 服务地区

核对时 OpenAI API 支持地区名单不包含中国大陆；官方说明，在支持地区之外访问或提供服务，可能导致账号被封禁或暂停。海外服务器并不能据此自动证明面向大陆用户提供服务符合条款。[来源：OpenAI API 支持地区](https://developers.openai.com/api/docs/supported-countries)

该来源针对 OpenAI API；ChatGPT/Codex 登录授权、第三方中转及其他服务商还需分别核对其实际条款与授权，不能凭接口兼容就推定合规。

## 以前的 QQ 群机器人是否在审核时限制 AI？

历史普通机器人提审流程和现在的扫码 Agent 入口应分开讨论：

- LangBot 旧版接入教程写明仅提供测试版机器人接入，并提醒：“目前 QQ 官方发布公告，禁止违规接入 AIGC 功能，请慎重使用。”这是**第三方教程对历史公告的转述**，不是本页已取得的官方公告原文，不能据此推导所有 AI 一律禁止。[来源：LangBot 旧版文档](https://docs.langbot.app/zh/usage/platforms/qq/official_webhook)
- 2024 年起的社区讨论提到企业认证、模型备案及上线审核；2026 年回复亦出现无资质接入的风险提醒。这些是**社区经验和意见，不是官方完整审核标准**，其中个别绕过审核的建议不应采用。[来源：Koishi 社区讨论及日期](https://forum.koishi.xyz/t/topic/9880)
- 当前 QQ 官方明确提供龙虾 Bot 对接 Hermes/OpenClaw 等 Agent 的指南，因此不应直接把旧资料概括为“现在龙虾 Bot 禁止接 AI”。但支持 Agent 接入仍不等于任意模型、资质和运营方式都已获批准。[来源：QQ 官方 Agent 接入](https://bot.q.qq.com/wiki/agent-qqbot/)

## 发布或反馈时不要暴露什么？

不要提交 AppSecret、访问令牌、真实群 OpenID、用户 ID、完整消息日志或含凭据截图。问题反馈保留脱敏后的 HTTP 状态、错误码、时间和触发条件即可；原始证据仅在必要且授权的安全渠道提供。
