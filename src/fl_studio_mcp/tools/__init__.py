"""Compatible registration entry points without eager tool-module imports."""

from importlib import import_module

_REGISTRATIONS = {
    "register_transport_tools": "transport",
    "register_mixer_tools": "mixer",
    "register_channel_tools": "channels",
    "register_plugin_tools": "plugins",
    "register_piano_roll_tools": "piano_roll",
}
__all__ = list(_REGISTRATIONS)


def __getattr__(name):
    module = _REGISTRATIONS.get(name)
    if module is None:
        raise AttributeError(f"{__name__} has no public export {name!r}")
    return getattr(import_module(f"{__name__}.{module}"), name)
