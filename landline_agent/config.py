"""landline-agent configuration (TOML), validated once at load."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .domain.prompt import PromptKit

PKG = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    principal: str
    hub_url: str
    hub_token_file: Path
    voice_model: str
    voice_name: str | None
    voice_key_file: Path
    allowlist_enabled: bool
    bind_brief: bool
    ask_ttl: int
    use_ttl: int
    host: str
    port: int
    approver_socket: Path
    requester_token_file: Path
    approver_token_file: Path
    data_dir: Path
    locale: str
    plugins_enabled: tuple[str, ...]
    dial_authority: frozenset[str]
    session_extension: frozenset[str]
    raw: dict
    nudge_after: float | None = 5.0      # callee finished, assistant still silent this long -> nudge
    quiet_after: float | None = 8.0      # line silent this long after the assistant spoke -> goodbye

    @property
    def calls_dir(self) -> Path:
        return self.data_dir / "calls"

    def secret(self, path: Path) -> str:
        return path.read_text().strip()

    def prompt_kit(self) -> PromptKit:
        p = PKG / "prompts" / self.locale
        pol = PKG / "policies" / self.locale
        policies = {}
        names = {"booking": "訂位"}          # task name used in briefs ->【任務】訂位
        for f in sorted(pol.glob("*.md")) if pol.exists() else []:
            policies[names.get(f.stem, f.stem)] = f.read_text(encoding="utf-8")
        return PromptKit(guard=(p / "guard.md").read_text(encoding="utf-8"),
                         style_casual=(p / "style_casual.md").read_text(encoding="utf-8"),
                         style_formal=(p / "style_formal.md").read_text(encoding="utf-8"),
                         policies=policies)


def _path(base: Path, v: str) -> Path:
    return (base / os.path.expanduser(v)).resolve()


def _opt_float(sec: dict, key: str, default):
    """Seconds; 0 or a missing key in a section that sets it to 0 turns the feature off."""
    v = sec.get(key, default)
    if v is None:
        return None
    v = float(v)
    if v < 0:
        raise ValueError(f"[call].{key} must be >= 0")
    return v or None


def load(path: str | Path) -> Config:
    path = Path(path)
    base = path.parent
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    pr = raw.get("principal", {})
    if not pr.get("name"):
        raise ValueError("[principal].name is required (who the assistant works for)")
    hub = raw.get("hub", {})
    voice = raw.get("voice", {})
    srv = raw.get("server", {})
    data = _path(base, srv.get("data_dir", "data"))
    plug = raw.get("plugins", {})
    if voice.get("provider", "gemini-live") != "gemini-live":
        raise ValueError("only voice.provider = 'gemini-live' is built in")
    return Config(
        principal=pr["name"],
        hub_url=hub.get("url", "http://127.0.0.1:8910"),
        hub_token_file=_path(base, hub.get("token_file", "data/hub-token")),
        voice_model=voice.get("model", "gemini-3.8-live"),
        voice_name=voice.get("voice"),
        voice_key_file=_path(base, voice.get("api_key_file", "data/gemini-key")),
        allowlist_enabled=bool(raw.get("allowlist", {}).get("enabled", True)),
        bind_brief=bool(raw.get("approval", {}).get("bind_brief", True)),
        ask_ttl=int(raw.get("approval", {}).get("ask_ttl_sec", 600)),
        use_ttl=int(raw.get("approval", {}).get("use_ttl_sec", 600)),
        host=srv.get("host", "127.0.0.1"), port=int(srv.get("port", 8920)),
        approver_socket=_path(base, srv.get("approver_socket", "data/approver.sock")),
        requester_token_file=_path(base, srv.get("requester_token_file", "data/requester-token")),
        approver_token_file=_path(base, srv.get("approver_token_file", "data/approver-token")),
        data_dir=data, locale=raw.get("prompts", {}).get("locale", "zh-TW"),
        plugins_enabled=tuple(plug.get("enabled", [])),
        dial_authority=frozenset(plug.get("dial_authority", [])),
        session_extension=frozenset(plug.get("session_extension", [])),
        raw=raw,
        nudge_after=_opt_float(raw.get("call", {}), "nudge_after_sec", 5.0),
        quiet_after=_opt_float(raw.get("call", {}), "quiet_hangup_sec", 8.0),
    )
