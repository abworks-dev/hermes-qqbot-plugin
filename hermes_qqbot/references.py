"""Extract the original group message's visual reference, never its quoted parent."""


def group_reference_index(event: dict) -> str | None:
    scene = event.get("message_scene")
    if not isinstance(scene, dict):
        return None
    extensions = scene.get("ext")
    if not isinstance(extensions, list):
        return None
    for item in extensions:
        if isinstance(item, str):
            key, separator, value = item.partition("=")
            if key == "msg_idx" and separator and value and len(value) <= 512:
                return value
    return None

