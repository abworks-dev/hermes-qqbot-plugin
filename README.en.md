# Hermes QQ Bot Plugin

> **Catalog status: not admitted to the official Hermes plugin catalog.** [Review on PR #139729 declined the listing under rule 15](https://github.com/NousResearch/hermes-agent/pull/139729#pullrequestreview-5557899756) because it replaces the bundled QQ adapter. Improvements are planned as small core contributions based on current upstream, not a renamed catalog submission. This repository and historical releases remain available; they are not an officially approved distribution, and planned contributions are not yet merged. This notice does not alter existing deployments.

[简体中文](README.md) · [Verification](VERIFICATION.md) · [Contributing](CONTRIBUTING.md)

**Connect Hermes Agent to the official QQ Bot API: private chats, group mentions, native buttons, media and optional C2C streaming.**

**Live demo: [recorded C2C exchange and post-restart group probe](DEMO.md)** — real observations, not a client screen recording; delivery limits are explicitly stated.

A community-maintained platform plugin, not an official Tencent or Nous Research product. Requires a QQ Open Platform bot account; this is not a personal-account QQ protocol client.

## Status and features

Early publication with controlled deployment and a recorded real C2C exchange. Automated results and limitations are documented in [VERIFICATION.md](VERIFICATION.md); not every host version, QQ permission or client is certified.

- C2C, group @, guild channels and guild DMs, with scene-specific routing.
- Text, Markdown, image, voice, video and file paths, subject to QQ permissions.
- Native clarification buttons: single/multiple choice, manual input and skip; numbered-text fallback when native delivery is unavailable.
- Optional C2C native streaming on compatible modern hosts; safe final-only buffering on legacy consumers.
- Long-message/table pagination, first-fragment group references and per-trigger reply accounting.
- Explicit menu/panel management and standalone-send CLI. Writes preview by default; `--apply` performs them.
- Profile-scoped secrets and conservative handling of unknown delivery acknowledgements: unknown does not mean safe to resend.

The plugin registers the same `qqbot` platform without importing/subclassing the bundled QQ adapter, rewriting host files or monkeypatching host functions.

## Install

1. Install [Hermes Agent](https://github.com/NousResearch/hermes-agent) with plugin CLI support. Create a separate test profile and a bot at [QQ Open Platform](https://q.qq.com/).
2. Replace `PROFILE` with your test profile name:

```sh
hermes -p PROFILE plugins install abworks-dev/hermes-qqbot-plugin
hermes -p PROFILE plugins list
```

Follow the host's dependency, security and authorization prompts. Dependencies are managed through Hermes PM, not manual pip changes to the host environment.

3. Store `QQ_APP_ID` and `QQ_CLIENT_SECRET` in the owning profile's secret store / `.env`. Never commit real credentials.
4. Merge, rather than replace, these settings:

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

Keep existing allowlists, tool policy and other plugins. Disable competing QQ overrides, including a retired `qq-c2c-streaming` installation. QQ Markdown and native keyboards require QQ-side permission. Clarification also requires the host's `clarify` tool.

5. Restart the gateway deliberately using your deployment's normal procedure. Shared gateway restarts may affect other profiles. Initiate a real authorized test and verify both logs and client receipt; test streaming, buttons and media separately.

## Compatibility and boundaries

No universal minimum released Hermes version is certified yet. Requires `PluginContext.register_platform`, `BasePlatformAdapter`, the canonical profile-scoped secret reader and shared clarify waiter. **`plugins.isolation: in_process` is mandatory** for platform registration. Progressive streaming additionally requires the synchronous prompt-boundary consumer API; legacy consumers buffer to a final reply.

Optional `send_message` integration requires an already enabled tool and an explicit grant under `plugins.entries.qqbot-standalone.allow_tool_override: true`. The plugin never enables a disabled messaging tool. Hardcoded stock cron / `hermes send` helpers in some host versions bypass the plugin; use the explicit plugin CLI for plugin-owned automation, subject to QQ authorization:

```sh
hermes -p PROFILE qqbot --help
hermes -p PROFILE qqbot menu-get
hermes -p PROFILE qqbot panel-list --scope c2c
hermes -p PROFILE qqbot menu-put --file menu.json          # preview
hermes -p PROFILE qqbot menu-put --file menu.json --apply  # replaces the ENTIRE menu
hermes -p PROFILE qqbot send qqbot:OPENID "hello"          # preview
hermes -p PROFILE qqbot send qqbot:OPENID "hello" --apply  # real send if authorized
```

An OpenID is not a QQ account number. The plugin cannot grant proactive-message permissions or bypass QQ quotas, URL restrictions or moderation. Unknown ACKs stop unsafe fallbacks rather than claiming success.

Shared host authorization, waiter, validation and setup APIs remain upgrade-review seams. No hidden compatibility layer repairs arbitrary host thread/provenance bugs. See the Chinese README for detailed configuration and [CONTRIBUTING.md](CONTRIBUTING.md) for regression commands.

## Pre-installation risk disclosure

- Connects to QQ REST/WebSocket services and sends authorized messages, interactions and media. Downloads inbound attachments and reads/uploads explicitly selected local media. Host-managed logs, temporary files and media paths extend outside the plugin directory.
- Audio transcription can send audio to the configured compatible STT service (such as Zhipu or OpenAI), with provider privacy and billing implications. Audio conversion may spawn an existing system `ffmpeg` executable without shell command interpolation; no binary is bundled.
- Maintains gateway WebSocket, heartbeat and reconnect tasks. Interactive setup persists credentials through host-provided profile save APIs, not this repository. Menu/panel mutations and explicit CLI sends require `--apply`.
- Replaces the bundled same-name QQ platform; optional tool override requires an explicit grant. Shared undocumented host-module imports remain compatibility risks. No self-updater or telemetry reporter is included.

## Feedback and license

[Issues](https://github.com/abworks-dev/hermes-qqbot-plugin/issues/new/choose) · [Changelog](CHANGELOG.md) · [Security](SECURITY.md)

Derived from Hermes QQ implementation history; original Nous Research copyright is retained under the [MIT License](LICENSE). Generic host stream consumers are not vendored. QQ and Hermes names belong to their respective owners; no endorsement is implied.
