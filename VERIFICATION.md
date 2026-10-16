# Verification / 验证范围

These are recorded pre-publication results, **not a fresh run on every public commit**. Fake HTTP tests do not prove QQ account permissions or client rendering.

## Automated regression evidence

Tests used the host's canonical `scripts/run_tests.sh`, temporary homes, clean credentials and per-file subprocess isolation.

| Matrix | Recorded result |
|---|---|
| Plugin suite against development host snapshot `6a1bad9bee` | 249 passed, 0 failed, 16 files |
| Plugin suite after retirement of duplicated host QQ patches | 249 passed, 0 failed, 16 files |
| Compatibility matrix with gateway exported from `c8cb89cc5005ae8df0e1ddb60ce19ae58fb6ada8` | 219 passed, 0 failed, 30 skipped |
| Expanded cleaned-host regression, outside actual Hermes home | 366 passed, 1 failed, 35 files |

Snapshot hashes identify development history; they are not a guarantee that the upstream public repository exposes those revisions. The frozen-gateway test uses current shared non-gateway modules, not a complete historical release. The 30 skips are modern progressive-stream tests: legacy consumers deliberately use buffered final delivery.

The remaining expanded-host failure also reproduced before cleanup: `test_real_shared_clarify_survives_detach` hit Windows child-output UTF-8 decoding, followed by an assertion-message `TypeError`. Therefore the expanded host suite is **not entirely green**. Running the checkout inside the real Hermes home also triggered the test guard protecting that home; do not disable that guard to make tests pass.

### Covered behavior

- Plugin discovery, platform replacement, factory ownership and permissioned tool override.
- Profile-scoped credentials; original/non-QQ tool delegation; target/media validation.
- Modern and legacy consumer behavior, empty native initialization, prompt boundaries and final receipts.
- C2C final ownership, unknown ACKs, connect rejection/refund and missing provenance.
- Choice/multiselect/manual/skip clarification, stale/foreign callback rejection and idempotence.
- Per-trigger reply quotas, expiry, scene routing, long-message/table partial outcomes and references.
- Management request shapes, offline preview and explicit writes.
- Standalone media/text ambiguity: timeouts, HTTP 503, malformed/empty/non-dict bodies and 204; no unsafe endpoint/caption replay. Success requires a real message ID.

## Live smoke evidence

- Controlled deployment recorded a real **private** inbound message, model response, acknowledged native final frame and suppression of a duplicate ordinary final reply. The operator confirmed receipt.
- After a later restart, the plugin reached QQ `Ready`. A real **group** probe recorded inbound processing, an eight-character generated response and entry into outbound sending. That log alone has no explicit final send ACK or client receipt confirmation.

Neither probe proves multi-frame progressive rendering, every button/media type, menu publication or all permissions. The repository contains no real credentials, recipient IDs or raw deployment logs. No live send is performed by installation or import.

## Reproduce

Clone the plugin beside a compatible `hermes-agent` checkout and use the host's isolated test environment. From the host checkout:

```sh
scripts/run_tests.sh ../hermes-qqbot-plugin/tests -j 4 -q
# Optional frozen-gateway matrix; export the recorded gateway tree first:
scripts/run_tests.sh ../hermes-qqbot-plugin/tests -j 4 -q -- --qq-host-source /absolute/frozen-host-root
```

Set `HERMES_PYTHON` to an appropriate isolated test interpreter where required. The test conftest resolves the sibling host by default. Do not use production credentials or disable home-I/O protection. See [CONTRIBUTING.md](CONTRIBUTING.md).
