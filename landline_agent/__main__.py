"""landline-agent command line.

  python -m landline_agent serve -c config.toml
  python -m landline_agent allow add NUMBER LABEL -c config.toml
  python -m landline_agent allow list|remove [NUMBER] -c config.toml
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

from . import config as config_mod


def _token(path: Path) -> str:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32))
        os.chmod(path, 0o600)
    return path.read_text().strip()


async def _serve(cfg) -> None:
    from aiohttp import web
    from .daemon.api import approver_app, requester_app
    from .daemon.service import AgentService
    from .plugins import load_plugins
    svc = AgentService(cfg)
    for w in svc.warnings():
        print(f"WARNING: {w}", flush=True)
    load_plugins(svc)
    runners = []
    r1 = web.AppRunner(requester_app(svc, _token(cfg.requester_token_file)))
    await r1.setup()
    await web.TCPSite(r1, cfg.host, cfg.port).start()
    runners.append(r1)
    where = f"requester API http://{cfg.host}:{cfg.port}"
    if cfg.allowlist_enabled:
        r2 = web.AppRunner(approver_app(svc, _token(cfg.approver_token_file)))
        await r2.setup()
        cfg.approver_socket.unlink(missing_ok=True)
        await web.UnixSite(r2, str(cfg.approver_socket)).start()
        os.chmod(cfg.approver_socket, 0o600)
        runners.append(r2)
        where += f", approver API unix:{cfg.approver_socket}"
    print(f"landline-agent: {where}", flush=True)
    asyncio.create_task(svc.expiry_loop())
    try:
        await asyncio.Event().wait()
    finally:
        for r in runners:
            await r.cleanup()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="landline-agent")
    ap.add_argument("command", choices=["serve", "allow"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("-c", "--config", default="config.toml")
    a = ap.parse_args(argv)
    cfg = config_mod.load(a.config)
    if a.command == "serve":
        asyncio.run(_serve(cfg))
        return 0
    from .daemon.store import AllowList
    al = AllowList(cfg.data_dir / "allowlist.json")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    sub = a.args[0] if a.args else "list"
    if sub == "add" and len(a.args) >= 3:
        print("allowed:", al.add(a.args[1], " ".join(a.args[2:])))
    elif sub == "remove" and len(a.args) == 2:
        print("removed" if al.remove(a.args[1]) else "not on the list")
    elif sub == "list":
        for n, label in al.load().items():
            print(f"{n:12} {label}")
    else:
        ap.error("allow add NUMBER LABEL | allow remove NUMBER | allow list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
