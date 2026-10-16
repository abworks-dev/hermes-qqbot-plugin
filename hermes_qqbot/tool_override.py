"""Optional whole-tool override preserving Hermes' canonical send validation.

Only an already exposed send_message tool is replaced. This module never registers
an absent tool, patches core globals, or replaces non-QQ dispatch. The QQ pipeline
below is migrated from tools/send_message_tool.py at 6a1bad9bee; review it whenever
the supported host changes its security/target/media validation pipeline.
"""
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


def register_override(ctx):
    from tools.registry import registry
    probe = getattr(ctx, "has_capability", None)
    if not callable(probe) or not probe("tools.override"):
        logger.warning("QQ adapter loaded; plugin-owned send_message integration unavailable: explicitly grant "
                       "plugins.entries.%s.allow_tool_override: true. Core direct QQ sends remain core-owned.",
                       getattr(ctx, "plugin_id", "qqbot-standalone"))
        return None
    entry = registry.get_entry("send_message")
    if entry is None:
        logger.warning("QQ adapter loaded; host exposes no send_message tool to override. "
                       "The plugin will not enable a tool disabled by host policy.")
        return None
    if (entry.dynamic_schema_overrides is not None or entry.execution_timeout is not None
            or entry.max_result_size_chars is not None):
        logger.warning("QQ adapter loaded; cannot safely preserve this host's extended send_message metadata; "
                       "tool override was not installed.")
        return None
    original = getattr(entry.handler, "__dict__", {}).get("_qqbot_original_handler", entry.handler)

    def handler(args, **kwargs):
        target = args.get("target", "")
        platform = target.partition(":")[0].strip().lower() if isinstance(target, str) else ""
        if platform == "qqbot" and args.get("action", "send") == "send":
            return _handle_send(args)
        return original(args, **kwargs)

    handler._qqbot_original_handler = original
    registered_handler = handler
    if entry.is_async:
        async def async_handler(args, **kwargs):
            target = args.get("target", "")
            platform = target.partition(":")[0].strip().lower() if isinstance(target, str) else ""
            if platform == "qqbot" and args.get("action", "send") == "send":
                return await asyncio.to_thread(_handle_send, args)
            return await original(args, **kwargs)
        async_handler._qqbot_original_handler = original
        registered_handler = async_handler
    from hermes_cli.plugins import PluginToolOverrideError
    try:
        return ctx.register_tool(
            name=entry.name, toolset=entry.toolset, schema=entry.schema, handler=registered_handler,
            check_fn=entry.check_fn, requires_env=entry.requires_env, is_async=entry.is_async,
            description=entry.description, emoji=entry.emoji, override=True,
        )
    except PluginToolOverrideError:
        # Consent may have changed since the probe. Platform registration still stands.
        logger.warning("QQ adapter loaded, but send_message override permission was denied.")
        return None


async def _send_to_platform(platform, pconfig, chat_id, message, *, thread_id=None,
                            media_files=None, force_document=False, **kwargs):
    from gateway.platforms.base import BasePlatformAdapter
    from tools.send_message_senders import _media_caption_split
    from .constants import MAX_MESSAGE_LENGTH
    from .senders import standalone_sender
    caption, separate = _media_caption_split(message, media_files, max_caption_len=MAX_MESSAGE_LENGTH)
    if caption is not None:
        return await standalone_sender(pconfig, chat_id, "", media_files=media_files, caption=caption,
                                       force_document=force_document)
    chunks = BasePlatformAdapter.truncate_message(separate, MAX_MESSAGE_LENGTH)
    result = None
    for index, chunk in enumerate(chunks):
        result = await standalone_sender(pconfig, chat_id, chunk, thread_id=thread_id,
                                         media_files=media_files if index == len(chunks) - 1 else None,
                                         force_document=force_document)
        if result.get("error"):
            return result
    return result or {"error": "No deliverable text or media remained"}


def _handle_send(args):
    import tools.send_message_tool as core
    target, message = (args.get('target', ''), args.get('message', ''))
    if not target or not message:
        return core.tool_error("Both 'target' and 'message' are required when action='send'")
    from agent.message_sanitization import _sanitize_surrogates
    message = _sanitize_surrogates(message)
    platform_name, chat_id, thread_id, resolution_error = core._resolve_tool_target(target)
    if resolution_error:
        return core.tool_error(resolution_error)
    if platform_name != 'qqbot':
        return core.tool_error("This plugin entry point only sends to qqbot targets")
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return core.tool_error('Interrupted')
    try:
        from gateway.config import load_gateway_config
        config = load_gateway_config()
    except Exception as e:
        return json.dumps(core._error(f'Failed to load gateway config: {e}'))
    platform, pconfig, entry, err = core._resolve_platform_config(platform_name, config)
    if err:
        return core.tool_error(err)
    from gateway.platforms.base import BasePlatformAdapter
    force_document_attachments = '[[as_document]]' in message
    media_files, cleaned_message = BasePlatformAdapter.extract_media(message)
    media_dropped: list = []
    media_files = BasePlatformAdapter.filter_media_delivery_paths(media_files, dropped=media_dropped)
    mirror_text = cleaned_message.strip() or core._describe_media_for_mirror(media_files)
    used_home_channel = not chat_id
    if used_home_channel:
        chat_id, err = core._home_chat_id(config, platform, platform_name)
        if err:
            return core.tool_error(err)
    if (duplicate_skip := core._maybe_skip_cron_duplicate_send(platform_name, chat_id, thread_id)):
        return json.dumps(duplicate_skip)
    if platform_name == 'slack' and chat_id:
        chat_id, resolve_err = core._slack_dm_chat_id(pconfig, chat_id)
        if resolve_err:
            return json.dumps(resolve_err)
    _relay_denial = core._authorize_relay_target(platform_name, chat_id, thread_id, native_token=getattr(pconfig, 'token', None))
    if _relay_denial:
        return core.tool_error(_relay_denial)
    try:
        from model_tools import _run_async
        handler_args = {'args': args} if entry is not None and entry.send_message_handler is not None else {}
        mentions = args.get('mentions')
        if mentions and platform_name == 'whatsapp':
            handler_args['mentions'] = [mentions] if isinstance(mentions, str) else list(mentions)
        result = _run_async(_send_to_platform(platform, pconfig, chat_id, cleaned_message, thread_id=thread_id, media_files=media_files, force_document=force_document_attachments, **handler_args))
        if isinstance(result, dict) and result.get('success'):
            if used_home_channel:
                result['note'] = f'Sent to {platform_name} home channel (chat_id: {chat_id})'
            if mirror_text and core._mirror_sent_message(platform_name, chat_id, mirror_text, thread_id):
                result['mirrored'] = True
            if media_dropped:
                result['success'] = False
                result['partial_success'] = True
                result['error'] = f'Delivery incomplete: {len(media_dropped)} requested MEDIA attachment(s) dropped before delivery (see media_dropped)'
        if isinstance(result, dict) and media_dropped:
            result['media_dropped'] = media_dropped
        if isinstance(result, dict) and 'error' in result:
            result['error'] = core._sanitize_error_text(result['error'])
        return json.dumps(result)
    except Exception as e:
        return json.dumps(core._error(f'Send failed: {e}'))
