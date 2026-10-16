"""Hermes QQ standalone plugin loader entry point."""


def register(ctx):
    from .hermes_qqbot.plugin import register as register_plugin
    return register_plugin(ctx)


__all__ = ["register"]
