# Changelog

## 1.0.0 — Initial standalone publication

The plugin manifest uses version `1.0.0`. This entry describes the initial source publication, not a guarantee of universal production compatibility.

- Independent QQ platform registration, profile-scoped secrets and explicit permissioned messaging-tool integration.
- Native clarification/approval buttons, reply accounting, scene-aware routing, table pagination and group references.
- Optional C2C native streaming with confirmed final ownership; safe buffered delivery on legacy consumers.
- Media delivery with conservative unknown-ACK handling, explicit standalone send and menu/panel CLI.
- Bilingual setup, compatibility boundaries, security guidance and regression evidence.

Known limitations: progressive streaming requires a compatible host API; some stock direct/cron send paths bypass plugin routing; QQ permissions and client rendering need separate live checks. See [README](README.md) and [VERIFICATION](VERIFICATION.md).
