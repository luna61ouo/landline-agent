"""Plugin loading.

Plugins are separately installed Python packages that expose an entry point
in the group "landline_agent.plugins".  The entry point is a callable
`setup(ctx)`.  Plugins run inside the daemon with its privileges, so only the
operator installs them, and the two powerful capabilities must also be
granted by name in the config:

  [plugins]
  enabled = ["emergency"]
  dial_authority = ["emergency"]       # may obtain DialPermits on its own
  session_extension = ["emergency"]    # may add tools, replace instructions, inject text mid-call

Nothing in the request API can load, enable or reach a plugin.
"""
from __future__ import annotations

from importlib.metadata import entry_points


class PluginContext:
    def __init__(self, name: str, svc):
        self.name, self._svc = name, svc
        self.cfg = svc.cfg

    @property
    def may_dial(self) -> bool:
        return self.name in self.cfg.dial_authority

    @property
    def may_extend_sessions(self) -> bool:
        return self.name in self.cfg.session_extension

    def place_call(self, request: dict, *, tools=None, instructions=None, inject=None, tool_handlers=None,
                   line=None, skip_number_format=False, **engine_opts) -> dict:
        """Dial on the plugin's own authority.  Requires dial_authority; any
        session customisation additionally requires session_extension."""
        custom = any(x is not None for x in (tools, instructions, inject, tool_handlers))
        if custom and not self.may_extend_sessions:
            return {"ok": False, "error": f"plugin {self.name!r} is not listed under [plugins].session_extension"}
        opts = dict(engine_opts)
        for k, v in (("tools", tools), ("instructions", instructions), ("inject", inject),
                     ("tool_handlers", tool_handlers), ("line", line)):
            if v is not None:
                opts[k] = v
        return self._svc.plugin_call(self.name, request, skip_number_format=skip_number_format, **opts)

    def call_status(self, call_id: str):
        return self._svc.status(call_id)


def load_plugins(svc) -> dict[str, object]:
    wanted = set(svc.cfg.plugins_enabled)
    loaded = {}
    for ep in entry_points(group="landline_agent.plugins"):
        if ep.name not in wanted:
            continue
        setup = ep.load()
        loaded[ep.name] = setup(PluginContext(ep.name, svc))
        svc.log("plugin_loaded", name=ep.name, dial_authority=ep.name in svc.cfg.dial_authority,
                session_extension=ep.name in svc.cfg.session_extension)
    missing = wanted - set(loaded)
    if missing:
        raise RuntimeError(f"plugins enabled in config but not installed: {sorted(missing)}")
    svc.plugins = loaded
    return loaded
