# Contributing / 贡献指南

Issues and pull requests in Chinese or English are welcome. Start with a small reproducible case; describe host version, operating system, plugin version and QQ scene. Redact credentials, OpenIDs, group IDs and private message content.

## Development setup

Clone [Hermes Agent](https://github.com/NousResearch/hermes-agent) and this repository as sibling directories named `hermes-agent` and `hermes-qqbot-plugin`. Follow the host's contribution instructions to prepare an isolated test interpreter through its package manager. The plugin is loaded by Hermes, not a standalone PyPI package.

From the host checkout:

```sh
scripts/run_tests.sh ../hermes-qqbot-plugin/tests -j 4 -q
```

Use the canonical host runner rather than bare pytest. Set `HERMES_PYTHON` if needed; never test against production bot credentials or actual profile state. See [VERIFICATION.md](VERIFICATION.md) for the optional frozen-gateway matrix and the limits of recorded results.

## Review expectations

- Keep QQ transport/protocol behavior inside this plugin; do not add a dependency on `gateway.platforms.qqbot`, monkeypatch host internals or rewrite host files.
- Cover changes with behavioral tests, including definite rejection versus ambiguous acceptance where delivery is involved.
- Unknown delivery must not trigger duplicate text/media/card fallback. Do not fabricate successful message IDs.
- Maintain profile isolation, callback authorization and explicit tool-override permission.
- Management writes must remain preview-first and require `--apply`.
- Preserve non-QQ tool delegation and current tool metadata.
- Update both README languages when behavior/configuration changes. Retain original license attribution.

Do not attach raw profile exports, tokens, `.env`, production conversation databases or unredacted logs to an issue. Security-sensitive reports follow [SECURITY.md](SECURITY.md).

For rollback, restore the previous profile plugin configuration and restart deliberately. Do not delete credentials or conversation history. Running gateways may share profiles; coordinate maintenance windows.
