# Hermes QQ Bot Plugin

> **目录状态：未获 Hermes 官方插件目录收录。** [维护者在 PR #139729 中依据规则 15 拒绝收录](https://github.com/NousResearch/hermes-agent/pull/139729#pullrequestreview-5557899756)：本项目替换内置 QQ 适配器。后续方向是基于最新上游，分批向核心贡献改进，而非通过改名重新申请目录。仓库与历史版本暂时保留；这不是官方认可的发行版，也不表示核心改进已合并。此公告不修改任何现有部署。

[English](README.en.md) · [安装与兼容性](#安装与兼容性) · [验证范围](VERIFICATION.md) · [贡献指南](CONTRIBUTING.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Hermes plugin](https://img.shields.io/badge/Hermes-platform_plugin-orange.svg)](https://github.com/NousResearch/hermes-agent)

**让 Hermes Agent 接入 QQ 官方机器人：群聊、私聊、原生交互按钮、媒体消息与可选私聊流式回复。**

**真实演示：[QQ 私聊原生最终帧与重启后群聊测试记录](DEMO.md)**（实际运行证据，非客户端录屏；覆盖边界明确标注）。

社区维护的独立平台插件，非腾讯或 Nous Research 官方产品。不使用个人 QQ 号协议；需要 QQ 开放平台机器人账号及相应权限。

> **状态：早期发布，已有受控环境部署与真实私聊收发记录。** 自动化测试覆盖范围见[验证说明](VERIFICATION.md)。这不是所有 Hermes 版本、QQ 权限或客户端的兼容保证；首次安装请用独立测试 profile。

## 为什么使用独立插件？

QQ 功能集中维护在一个仓库，通过 Hermes 的平台注册机制替换同名 `qqbot` 适配器。不继承或导入内置 QQ 适配器，不修改宿主源文件，不 monkeypatch 宿主函数。

| 能力 | 支持范围 / 限制 |
|---|---|
| 私聊、群 @、频道及频道私信 | 按场景路由；需开通对应事件与权限，各场景并非都完成真实账号测试 |
| 文本、Markdown、图片、语音、视频、文件 | 受 QQ 消息类型权限、文件限制与审核约束 |
| 原生按钮与澄清卡片 | 单选、多选、手动输入、跳过；原生权限不可用时可回退编号文本 |
| 私聊原生流式 | 需宿主同步 prompt-boundary API；旧消费者安全退化为完整最终回复 |
| 长消息、表格分页、群引用 | 分片投递；不重放已确认送达的前半段 |
| 回复额度与投递状态 | 按触发消息计数；不把过期被动回复偷偷转为主动推送 |
| 菜单与面板管理 CLI | 写操作默认仅预览；`--apply` 才执行 |
| 多 profile 凭据隔离 | 使用宿主 profile-scoped secret reader；不复制凭据到仓库 |

**投递结果未知不等于失败。** 响应超时或 ACK 不明确时，插件避免盲目重发、重复卡片或重复媒体说明；这也意味着不会假装消息已送达。

## 安装与兼容性

### 1. 准备条件

- 已安装 [Hermes Agent](https://github.com/NousResearch/hermes-agent)，可使用其插件 CLI。
- 在 [QQ 开放平台](https://q.qq.com/) 获取 `QQ_APP_ID`、`QQ_CLIENT_SECRET`，配置机器人事件、沙箱成员与所需权限。
- 宿主具备 `PluginContext.register_platform`、`BasePlatformAdapter`、profile-scoped secret reader 和共享 clarify waiter。
- 使用 `plugins.isolation: in_process`；隔离插件进程不能完成平台注册。

**暂不声明通用最低 Hermes 发布版本。** 当前验证基于开发快照及部分旧 gateway 兼容矩阵，不是所有官方 release 的完整测试。渐进式流式还需要同步 prompt-boundary API；旧宿主只能获得最终完整回复。宿主升级后请重新跑回归。

### 2. 在独立 profile 安装

下面的 `PROFILE` 请替换为你创建的测试 profile 名称：

```sh
hermes -p PROFILE plugins install abworks-dev/hermes-qqbot-plugin
hermes -p PROFILE plugins list
```

遵循宿主依赖安装、安全扫描及授权提示。插件依赖由 Hermes PM 管理，不需要对宿主环境手工 `pip install`。若你的宿主没有对应 CLI 或共享 API，请先检查宿主兼容性，不要绕过校验复制文件到核心目录。

在**该 profile** 的 secret store / `.env` 中配置以下凭据，不要提交到 Git：

```dotenv
QQ_APP_ID=YOUR_QQ_APP_ID
QQ_CLIENT_SECRET=YOUR_QQ_APP_SECRET
```

### 3. 合并配置

将下列内容**合并**到测试 profile 的配置，不要覆盖其他插件、模型、allowlist 或工具策略：

```yaml
plugins:
  isolation: in_process
  enabled:
    - qqbot-standalone
platforms:
  qqbot:
    enabled: true
    extra:
      native_streaming: true
      markdown_support: true
```

仅保留一个活跃 QQ 平台实现。特别是已有 `qq-c2c-streaming` 等 QQ override 时，先停用竞争实现。保留现有 DM/群授权规则；`enabled: true` 不会自动授予所有人访问权限。

可选：若宿主本来已启用 `send_message`，并希望 QQ 工具调用也由插件处理，可在 `plugins` 下合并以下**显式特权授权**：

```yaml
entries:
  qqbot-standalone:
    allow_tool_override: true
```

插件不会自动启用已禁用的 messaging 工具。澄清功能同样要求宿主启用 `clarify` 工具；QQ Markdown/键盘权限需要 QQ 侧单独开通。

### 4. 重启与测试

在维护窗口按你的宿主部署方式重启网关；共享网关重启可能影响其他 profile。然后主动给机器人发送测试消息，核对：入站日志、模型回复、真实客户端收件和是否重复回复。渐进式多帧、按钮、媒体、群消息应分别测试，不能用一条文本回复代替全部验收。

## 命令示例

```sh
hermes -p PROFILE qqbot --help
hermes -p PROFILE qqbot menu-get
hermes -p PROFILE qqbot panel-list --scope c2c
hermes -p PROFILE qqbot menu-put --file menu.json          # 仅预览
hermes -p PROFILE qqbot menu-put --file menu.json --apply  # 替换整个全局菜单
hermes -p PROFILE qqbot send qqbot:OPENID "hello"          # 仅预览
hermes -p PROFILE qqbot send qqbot:OPENID "hello" --apply  # 实际发送，受 QQ 授权约束
```

`OPENID` 必须来自合法授权上下文，不是 QQ 号码。主动发送是否允许由 QQ 当前接口、账号权限和额度决定；插件不能绕过平台限制。面板还提供 create/get/update/targets/delete，见 CLI 帮助。

## 重要限制

- **不是所有发送路径都自动替换。** 正常网关回复使用插件适配器；可选 `send_message` override 只替换已存在且获授权的工具。某些宿主版本的 stock cron / `hermes send` 硬编码调用内置 QQ sender，会绕过插件。需要插件拥有的自动化发送时，使用显式 `hermes qqbot send ... --apply` 并遵守授权。
- **不是全版本无依赖插件。** 依赖共享授权、凭据、waiter、目标/媒体验证及 setup API；升级宿主后需回归。不会隐式修复宿主跨线程队列或缺失 provenance。
- **不是无限主动推送工具。** 回复窗口、计数、URL、模板和媒体权限以 QQ 当前政策为准；本地 ledger 不能扩大远端额度。
- 本项目不承诺未获得权限的 Markdown、按钮、菜单或多帧渲染可用。不提供在线演示账号或内置密钥。

## 安装前风险披露

- 运行时连接 QQ REST/WebSocket 服务并发送授权的消息、交互及媒体；收到的附件会下载到本地，指定的本地媒体文件会被读取并上传。日志和临时/媒体文件由宿主相关路径管理，不只读写插件目录。
- 语音转写可能将音频发送给配置的兼容 STT 服务（例如 Zhipu 或 OpenAI）；请先确认数据处理政策与账号费用。转换音频时可调用系统已有的 `ffmpeg` 子进程，不使用 shell 命令拼接；本插件不附带该二进制。
- 网关内会运行持久 WebSocket、心跳和重连任务。交互式配置向导通过宿主凭据保存接口写入 profile 配置；不把秘密写入本仓库。菜单/面板及显式发送只有用户使用 `--apply` 才写入远端。
- 本插件替换同名内置 QQ 适配器；可选工具 override 需显式授权。依赖部分未文档化的宿主共享模块，升级有兼容风险。没有自更新器，不附带遥测上报。

## 开发与反馈

- [英文说明](README.en.md) · [验证记录](VERIFICATION.md) · [更新日志](CHANGELOG.md)
- [贡献流程](CONTRIBUTING.md) · [安全报告](SECURITY.md)
- [报告问题](https://github.com/abworks-dev/hermes-qqbot-plugin/issues/new/choose)

本项目是 Hermes 插件，不是独立机器人服务或 PyPI 安装包。测试依赖一个相邻的 Hermes checkout，具体命令见贡献指南。

## 致谢与许可

QQ 实现及回归从 Hermes 开发历史中迁移，保留 Nous Research 原始版权及 [MIT License](LICENSE)。通用宿主消费者未被打包到本插件。`QQ`、`Hermes` 等名称属于各自权利人；本仓库不表示官方背书。
