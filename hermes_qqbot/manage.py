"""Explicit QQ management CLI; writes are offline previews unless --apply is given."""

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class RequestPlan:
    method: str
    path: str
    operation: str
    args: tuple
    payload: dict[str, Any] | None = None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    setup_parser(parser)
    return parser.parse_args(argv)


def setup_parser(parser):
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send", help="Send through the plugin-owned validated QQ pipeline")
    send.add_argument("target", help="qqbot:OPENID or qqbot:directory-name")
    send.add_argument("message", help="Text, optionally containing MEDIA tags")
    send.add_argument("--apply", action="store_true", help="Execute the remote send")
    commands.add_parser("menu-get")
    listing = commands.add_parser("panel-list")
    listing.add_argument("--scope", choices=("c2c", "group", "channel", "dm"), required=True)
    for name in ("menu-put", "panel-create", "panel-get", "panel-update", "panel-delete", "panel-targets"):
        command = commands.add_parser(name)
        if name.startswith("panel-") and name != "panel-create":
            command.add_argument("panel_id")
        if name != "panel-get":
            command.add_argument("--apply", action="store_true", help="Execute the remote write")
            if name != "panel-delete":
                command.add_argument("--file", type=Path, required=True, help="UTF-8 JSON object")


def build_plan(args) -> RequestPlan:
    payload = None
    if getattr(args, "file", None) is not None:
        try:
            payload = json.loads(args.file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise ValueError("--file must be a readable UTF-8 JSON object") from None
        if not isinstance(payload, dict):
            raise ValueError("--file must contain a JSON object")
    panel_id = getattr(args, "panel_id", "")
    path = f"/v2/panels/{quote(panel_id, safe='')}"
    specs = {
        "menu-get": ("GET", "/v2/menu", "get_global_menu", ()),
        "menu-put": ("PUT", "/v2/menu", "update_global_menu", (payload,)),
        "panel-list": ("GET", f"/v2/panels?scope={getattr(args, 'scope', '')}", "list_panels", (getattr(args, "scope", ""),)),
        "panel-create": ("POST", "/v2/panels", "create_panel", (payload,)),
        "panel-get": ("GET", path, "get_panel", (panel_id,)),
        "panel-update": ("PUT", path, "update_panel", (panel_id, payload)),
        "panel-delete": ("DELETE", path, "delete_panel", (panel_id,)),
        "panel-targets": ("PUT", path + "/target", "update_panel_targets", (panel_id, payload)),
    }
    return RequestPlan(*specs[args.command], payload=payload)


async def execute_plan(plan: RequestPlan, client=None):
    """Dispatch against an injected management client, or open only a REST client."""
    if client is not None:
        return await getattr(client, plan.operation)(*plan.args)

    import httpx
    from gateway.config import Platform, PlatformConfig, load_gateway_config
    from .adapter import QQAdapter

    config = load_gateway_config().platforms.get(Platform.QQBOT, PlatformConfig())
    adapter = QQAdapter(config)
    if not adapter._app_id or not adapter._client_secret:
        raise ValueError("QQ credentials are not configured")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
        adapter._http_client = http
        try:
            return await execute_plan(plan, adapter.management)
        finally:
            adapter._http_client = None


def main(argv=None, *, client=None) -> int:
    return run_args(parse_args(argv), client=client)


def run_args(args, *, client=None) -> int:
    if args.command == "send":
        return _run_send(args)
    try:
        plan = build_plan(args)
    except ValueError as exc:
        print(f"qqbot manage: {exc}", file=sys.stderr)
        return 2
    if plan.method != "GET" and not args.apply:
        print(json.dumps({"dry_run": True, "method": plan.method, "path": plan.path,
                          "payload": plan.payload}, ensure_ascii=False, indent=2))
        return 0
    try:
        result = asyncio.run(execute_plan(plan, client))
    except Exception:
        # Transport/config exceptions can contain credentials and raw QQ responses.
        print("qqbot manage: request failed; check configuration, permissions and connectivity", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _run_send(args) -> int:
    if args.target.partition(":")[0].strip().lower() != "qqbot":
        print("qqbot send: target must use the qqbot: prefix", file=sys.stderr)
        return 2
    if not args.apply:
        print(json.dumps({"dry_run": True, "action": "send", "target": args.target,
                          "message": args.message}, ensure_ascii=False, indent=2))
        return 0
    from .tool_override import _handle_send
    try:
        result = json.loads(_handle_send({"action": "send", "target": args.target, "message": args.message}))
    except Exception:
        print("qqbot send: request failed; check configuration and permissions", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") and not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
