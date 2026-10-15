"""Two HTTP surfaces, two credentials, two listeners.

Requester API (TCP, requester token) - for the AI or any program:
  POST /v1/calls                {number, brief, label, purpose, max_sec}
  GET  /v1/calls/{id}           state, and the transcript when done
  GET  /v1/calls/{id}/events    SSE
  PATCH /v1/calls/{id}          {brief}  revise while awaiting approval
  POST /v1/calls/{id}/cancel

Approver API (unix socket, approver token) - only for approval front-ends:
  GET  /v1/approvals            pending tickets (number, who, why, full brief)
  GET  /v1/approvals/stream     SSE of new tickets
  POST /v1/approvals/{id}/decision   {approve: bool, decided_by: str}

The approver API is not served at all while the allowlist is off: with no
allowlist there is nothing to approve, every call is dialled automatically.
"""
from __future__ import annotations

import asyncio
import json
import secrets

from aiohttp import web

from .service import AgentService


def _guard(token: str):
    @web.middleware
    async def mw(request, handler):
        h = request.headers.get("Authorization", "")
        if not (h.startswith("Bearer ") and secrets.compare_digest(h[7:], token)):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)
    return mw


async def _sse(request, listeners: set, first=()):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    listeners.add(q)
    try:
        for ev in first:
            await resp.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
                await resp.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        listeners.discard(q)
    return resp


def requester_app(svc: AgentService, token: str) -> web.Application:
    app = web.Application(middlewares=[_guard(token)])

    async def create(request):
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "errors": ["body must be JSON"]}, status=400)
        r = svc.request(body)
        return web.json_response(r, status=200 if r["ok"] else 400)

    async def get(request):
        r = svc.status(request.match_info["cid"])
        return web.json_response(r) if r else web.json_response({"error": "no_such_call"}, status=404)

    async def events(request):
        cid = request.match_info["cid"]
        st = svc.status(cid)
        if not st:
            raise web.HTTPNotFound()
        return await _sse(request, svc.req_listeners, first=[{"type": "call", **st}])

    async def revise(request):
        body = await request.json()
        r = svc.update_brief(request.match_info["cid"], str(body.get("brief", "")))
        return web.json_response(r, status=200 if r["ok"] else 409)

    async def cancel(request):
        r = await svc.cancel(request.match_info["cid"])
        return web.json_response(r, status=200 if r["ok"] else 409)

    app.router.add_post("/v1/calls", create)
    app.router.add_get("/v1/calls/{cid}", get)
    app.router.add_get("/v1/calls/{cid}/events", events)
    app.router.add_post("/v1/calls/{cid}/cancel", cancel)
    app.router.add_patch("/v1/calls/{cid}", revise)
    return app


def approver_app(svc: AgentService, token: str) -> web.Application:
    app = web.Application(middlewares=[_guard(token)])

    async def pending(request):
        return web.json_response(svc.pending())

    async def stream(request):
        return await _sse(request, svc.approver_listeners, first=svc.pending())

    async def decision(request):
        body = await request.json()
        r = svc.decide(request.match_info["tid"], bool(body.get("approve")), str(body.get("decided_by", ""))[:60])
        return web.json_response(r, status=200 if r["ok"] else 409)

    app.router.add_get("/v1/approvals", pending)
    app.router.add_get("/v1/approvals/stream", stream)
    app.router.add_post("/v1/approvals/{tid}/decision", decision)
    return app
