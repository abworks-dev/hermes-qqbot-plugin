"""Supported Hermes plugin registrations, with no core mutation."""
import importlib.util
import logging

logger = logging.getLogger(__name__)


def _factory(config):
    from .adapter import QQAdapter
    return QQAdapter(config)


def _check_dependencies():
    return all(importlib.util.find_spec(name) is not None for name in ("httpx", "aiohttp"))


def _credentials(config):
    from gateway.platforms._shared import get_scoped_secret
    extra = config.extra or {}
    return bool((extra.get("app_id") or get_scoped_secret("QQ_APP_ID", "")) and
                (config.token or extra.get("client_secret") or get_scoped_secret("QQ_CLIENT_SECRET", "")))


def _env_enablement():
    from gateway.platforms._shared import get_scoped_secret
    app_id = get_scoped_secret("QQ_APP_ID", "")
    secret = get_scoped_secret("QQ_CLIENT_SECRET", "")
    if not app_id or not secret:
        return None
    result = {"app_id": app_id, "client_secret": secret}
    for key, env in (("allow_from", "QQ_ALLOWED_USERS"), ("group_allow_from", "QQ_GROUP_ALLOWED_USERS")):
        value = get_scoped_secret(env, "")
        if value:
            result[key] = value.strip()
    return result


def _cli_setup(parser):
    from .manage import setup_parser
    setup_parser(parser)


def _cli_run(args):
    from .manage import run_args
    return run_args(args)


def register(ctx):
    from .senders import standalone_sender
    ctx.register_platform(
        name="qqbot", label="QQ Bot", adapter_factory=_factory,
        check_fn=_check_dependencies, validate_config=_credentials, is_connected=_credentials,
        required_env=["QQ_APP_ID", "QQ_CLIENT_SECRET"],
        install_hint="Install this plugin's declared dependencies using Hermes PM.",
        setup_fn=setup_qqbot, allowed_users_env="QQ_ALLOWED_USERS", allow_all_env="QQ_ALLOW_ALL_USERS",
        emoji="🐧", max_message_length=4000,
        cron_deliver_env_var="QQBOT_HOME_CHANNEL", env_enablement_fn=_env_enablement,
        standalone_sender_fn=standalone_sender,
        platform_hint=("You are on QQ, a popular Chinese messaging platform. QQ supports markdown formatting "
                       "and emoji. You can send media files natively: include MEDIA:/absolute/path/to/file in "
                       "your response. Images are sent as native photos, and other files arrive as downloadable documents."),
    )
    ctx.register_cli_command("qqbot", "Manage QQ menus and panels (writes preview by default)",
                             _cli_setup, _cli_run)
    from .tool_override import register_override
    register_override(ctx)


def setup_qqbot():
    from hermes_cli.gateway_setup_wizard import (
        _print_setup_header, _confirm_reconfigure, _gw, _save_env_values, _prompt_csv, _offer_home_channel,
    )
    """Interactive setup for QQ Bot — scan-to-configure or manual credentials."""
    _print_setup_header("🐧 QQ Bot")

    if not _confirm_reconfigure("QQ Bot", "QQ_APP_ID", "QQ_CLIENT_SECRET"):
        return

    print()
    method_choices = ["Scan QR code to add bot automatically (recommended)", "Enter existing App ID and App Secret manually"]
    credentials = None
    if _gw().prompt_choice("  How would you like to set up QQ Bot?", method_choices, 0) == 0:
        try:
            from .onboard import qr_register
            credentials = qr_register()
        except KeyboardInterrupt:
            print()
            _gw().print_warning("  QQ Bot setup cancelled.")
            return
        if not credentials:
            _gw().print_info("  QR setup did not complete. Continuing with manual input.")

    if not credentials:
        print()
        _gw()._print_info_lines(
            "  Go to https://q.qq.com to register a QQ Bot application.",
            "  Note your App ID and App Secret from the application page.",
        )
        print()
        app_id = _gw().prompt("  App ID", password=False)
        if not app_id:
            _gw().print_warning("  Skipped — QQ Bot won't work without an App ID.")
            return
        app_secret = _gw().prompt("  App Secret", password=True)
        if not app_secret:
            _gw().print_warning("  Skipped — QQ Bot won't work without an App Secret.")
            return
        credentials = {"app_id": app_id.strip(), "client_secret": app_secret.strip(), "user_openid": ""}

    _gw().save_env_value("QQ_APP_ID", credentials["app_id"])
    _gw().save_env_value("QQ_CLIENT_SECRET", credentials["client_secret"])

    user_openid = credentials.get("user_openid", "")

    print()
    access_choices = ["Use DM pairing approval (recommended)", "Allow all direct messages", "Only allow listed user OpenIDs"]
    access_idx = _gw().prompt_choice("  How should direct messages be authorized?", access_choices, 0)
    if access_idx == 0:
        _gw().save_env_value("QQ_ALLOW_ALL_USERS", "false")
        allowed = ""
        if user_openid:
            print()
            if _gw().prompt_yes_no(f"  Add yourself ({user_openid}) to the allow list?", True):
                allowed = user_openid
                _gw().print_success(f"  Allow list set to {user_openid}")
        _gw().save_env_value("QQ_ALLOWED_USERS", allowed)
        _gw().print_success("  DM pairing enabled.")
        _gw().print_info("  Unknown users can request access; approve with `hermes pairing approve`.")
    elif access_idx == 1:
        _save_env_values(QQ_ALLOW_ALL_USERS="true", QQ_ALLOWED_USERS="")
        _gw().print_warning("  Open DM access enabled for QQ Bot.")
    else:
        allowlist = _prompt_csv("  Allowed user OpenIDs (comma-separated)", user_openid or "")
        _save_env_values(QQ_ALLOW_ALL_USERS="false", QQ_ALLOWED_USERS=allowlist)
        _gw().print_success("  Allowlist saved.")

    print()
    if user_openid:
        _offer_home_channel("QQBOT_HOME_CHANNEL", user_openid, "your QQ user ID")
    else:
        home_channel = _gw().prompt("  Home channel OpenID (for cron/notifications, or empty)", password=False)
        if home_channel:
            _gw().save_env_value("QQBOT_HOME_CHANNEL", home_channel.strip())
            _gw().print_success(f"  Home channel set to {home_channel.strip()}")

    print()
    _gw().print_success("🐧 QQ Bot configured!")
    _gw().print_info(f"  App ID: {credentials['app_id']}")


