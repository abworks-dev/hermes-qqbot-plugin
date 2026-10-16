"""Standalone QQ gateway integration; importing the package has no side effects."""


def register(ctx):
    from .plugin import register as register_plugin
    return register_plugin(ctx)


def __getattr__(name):
    if name == "QQAdapter":
        from .adapter import QQAdapter
        return QQAdapter
    raise AttributeError(name)


__all__ = ["QQAdapter", "register"]
