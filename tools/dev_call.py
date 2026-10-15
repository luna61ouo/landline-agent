"""Development only: place one call directly, the number treated as allowlisted.

  python tools/dev_call.py config.toml NUMBER brief.md [max_sec]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from landline_agent import config as config_mod  # noqa: E402
from landline_agent.domain import authorization as az  # noqa: E402
from landline_agent.domain.call_request import ValidatedCallRequest, validate  # noqa: E402
from landline_agent.engine.call import place_call  # noqa: E402


async def main():
    cfg = config_mod.load(sys.argv[1])
    req = validate({"number": sys.argv[2], "brief": Path(sys.argv[3]).read_text(encoding="utf-8"),
                    "label": "dev test", "purpose": "development call",
                    "max_sec": int(sys.argv[4]) if len(sys.argv) > 4 else 60})
    if not isinstance(req, ValidatedCallRequest):
        sys.exit(f"invalid: {req.errors}")
    permit = az.for_request(az.Settings(allowlist=frozenset({req.number})), req.number)
    log = await place_call(cfg, permit, req)
    print((log.dir / "transcript.txt").read_text(encoding="utf-8"))
    m = log.meta
    print(f"\n{m['duration_sec']} s | {m['end_reason']}/{m['ended_by']} | latency {m['latency_sec']} | ${m['cost_usd_rough']} | {log.dir}")


asyncio.run(main())
