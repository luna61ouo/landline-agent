"""Place one call: permit in, call record out."""
from __future__ import annotations

from ..config import Config
from ..domain import prompt as prompt_mod
from ..domain.authorization import DialPermit
from ..domain.call_request import ValidatedCallRequest
from ..lines.hub import HubLine
from ..record.calllog import CallLog
from ..voice.gemini_live import GeminiLive
from .tools import DEFAULT_TOOLS


async def place_call(cfg: Config, permit: DialPermit, req: ValidatedCallRequest, line=None,
                     tools=DEFAULT_TOOLS, instructions: str | None = None, **engine_opts) -> CallLog:
    if not isinstance(permit, DialPermit) or permit.number != req.number:
        raise PermissionError("a DialPermit for this exact number is required")
    log = CallLog(cfg.calls_dir, "call", req.label, cfg.voice_model, to=req.number, permit=permit.source,
                  ticket=permit.ticket_id, purpose=req.purpose)
    if instructions is None:
        instructions = prompt_mod.build(req.brief, cfg.prompt_kit(), cfg.principal)
    if line is None:
        line = HubLine(cfg.hub_url, cfg.secret(cfg.hub_token_file), log=lambda t, **f: log.event(t, **f))
    reason, by = "unknown", ""
    try:
        if not await line.dial(permit):
            reason, by = f"not_connected:{line.fail_reason}", "system"
            log.note("system", f"not connected ({line.fail_reason})")
        else:
            engine = GeminiLive(cfg.secret(cfg.voice_key_file), cfg.voice_model, cfg.voice_name)
            reason, by = await engine.run(line, instructions, log, tools, max_sec=req.max_sec, **engine_opts)
    except Exception as e:
        log.error("call", e)
        reason, by = "error", "system"
        await line.hangup()
    finally:
        log.close(reason, by)
        await line.close()
    return log
