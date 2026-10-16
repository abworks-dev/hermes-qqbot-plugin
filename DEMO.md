# 真实收发演示 / Recorded live exchange

这是受控账号测试的文字记录，**不是客户端录屏、模拟 UI 或完整功能认证**。为保护用户隐私，不公开账号、OpenID、session ID、凭据或原始日志。

## 场景一：QQ 私聊 → Hermes → QQ 原生最终帧

实际发送的测试消息：

> 上线验证-1249，请只回复‘收发正常’

预期回复：`收发正常`。

真实运行记录（同一次测试）：

| 时间 | 观察到的事件 |
|---|---|
| 12:50:51 | 独立 QQ 插件收到真实 C2C 私聊，网关在所属 profile 分发 |
| 12:51:03 | 插件收到原生最终帧 ACK：`index=1 final=True chars=4`，该帧耗时 513 ms |
| 随后 | 宿主根据已确认最终投递抑制第二次普通发送：`streamed=True`、`content_delivered=True` |
| 本轮结束 | 12.6 秒，1 次模型 API 调用，4 字回复；操作者确认已收到回复 |

这证明真实私聊入站、模型处理、原生最终帧确认、重复最终回复抑制，以及操作者可见收件。未在此发布完整回复正文日志，因此不把“预期文本”包装成逐字导出的客户端截图。

**不证明**多帧渐进式渲染、所有客户端、按钮、媒体或菜单权限。

## 场景二：重启后群聊探针

实际发送：

> 重启往返测试：请只回复“QQ重启验证成功”

| 时间 | 观察到的事件 |
|---|---|
| 14:20:36 | 收到真实群消息 |
| 14:20:52 | 回复生成，8 字，耗时 15.4 秒，1 次模型 API 调用 |
| 随后 | 网关进入发送流程 |

该轮没有明确最终发送 ACK 或操作者客户端收件确认，所以**只证明接收、处理和发起发送**，不宣称完成全程送达验收。

## 自己复现

1. 使用独立测试 profile，按 [README](README.md) 安装并保留访问控制。
2. 在维护窗口重启网关，确认插件连接并进入 `Ready`。
3. 在已授权 QQ 私聊中发送一个不含私人信息的唯一测试短句，要求固定简短回复。
4. 同时核对客户端收件、入站日志、最终 ACK 和是否重复。日志显示“Sending”不等于已收件。
5. 多帧流式、按钮、媒体、群聊分别验收，不要从一次文本测试推断所有能力可用。

若愿意提交录屏，请遮挡昵称、头像、群名、账号及其他聊天，保留测试输入与实际回复；不要附带凭据页面。

## English summary

These are recorded live observations, not a screen recording or simulated client UI. A controlled C2C exchange produced an acknowledged native final frame, duplicate-final suppression and operator-confirmed receipt. A later post-restart group probe only recorded inbound processing, an eight-character response and send initiation; no final client receipt is claimed for it. Multi-frame rendering, buttons, media and account permissions need separate verification.

See [VERIFICATION.md](VERIFICATION.md) for automated and live boundaries.
