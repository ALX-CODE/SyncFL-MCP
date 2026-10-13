"""Public utility exports, resolved only when a caller requests them."""

from importlib import import_module

_EXPORTS = {
    "FLConnection": "connection",
    "get_connection": "connection",
    "reset_connection": "connection",
    "FLStudioTrigger": "fl_trigger",
    "get_trigger": "fl_trigger",
    "trigger_fl_studio": "fl_trigger",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"{__name__} has no public export {name!r}")
    return getattr(import_module(f"{__name__}.{module}"), name)
