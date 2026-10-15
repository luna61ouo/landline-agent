"""The agent daemon: turns call requests into calls, with approval in between.

Requesters (an AI, a script) can only ask.  Dialling happens here, after a
DialPermit was obtained from the allowlist, an approved ticket, auto mode
(allowlist off) or an authorized plugin.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field

from ..config import Config
from ..domain import approval as ap
from ..domain import authorization as az
from ..domain.call_request import Invalid, ValidatedCallRequest, validate
from ..engine.call import place_call
from .store import AllowList, TicketStore


@dataclass
class CallJob:
    call_id: str
    req: ValidatedCallRequest
    state: str                      # awaiting_approval | denied | expired | queued | calling | done | cancelled
    created: float = field(default_factory=time.time)
    permit_source: str | None = None
    result: dict | None = None
    task: asyncio.Task | None = None
    line: object | None = None


class AgentService:
    def __init__(self, cfg: Config, log=None):
        self.cfg = cfg
        self.log = log or (lambda *a, **k: print(time.strftime("%H:%M:%S"), *a, k, flush=True))
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.tickets = TicketStore(cfg.data_dir / "tickets.json")
        self.allowlist = AllowList(cfg.data_dir / "allowlist.json")
        self.jobs: dict[str, CallJob] = {}
        self.dial_lock = asyncio.Lock()        # one call at a time: there is one line
        self.req_listeners: set[asyncio.Queue] = set()
        self.approver_listeners: set[asyncio.Queue] = set()
        self.plugins = None                    # set by the loader

    # ------------------------------------------------------------ settings
    def settings(self) -> az.Settings:
        return az.Settings(allowlist_enabled=self.cfg.allowlist_enabled, allowlist=self.allowlist.numbers(),
                           bind_brief=self.cfg.bind_brief, authorized_plugins=self.cfg.dial_authority)

    def warnings(self) -> list[str]:
        return az.startup_warnings(self.settings())

    # ------------------------------------------------------------ events
    def _emit(self, listeners: set, ev: dict) -> None:
        ev = {"at": time.time(), **ev}
        for q in list(listeners):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    def _job_event(self, job: CallJob, **extra) -> None:
        self._emit(self.req_listeners, {"type": "call", "call_id": job.call_id, "state": job.state, **extra})

    # ------------------------------------------------------------ requester side
    def request(self, raw: dict) -> dict:
        v = validate(raw)
        if isinstance(v, Invalid):
            return {"ok": False, "state": "rejected", "errors": list(v.errors)}
        call_id = time.strftime("%m%d%H%M%S-") + secrets.token_hex(3)
        job = CallJob(call_id, v, "queued")
        self.jobs[call_id] = job
        decision = az.for_request(self.settings(), v.number)
        if isinstance(decision, az.DialPermit):
            self._start(job, decision)
            return {"ok": True, "call_id": call_id, "state": job.state, "permit": decision.source}
        job.state = "awaiting_approval"
        t = ap.open_ticket(call_id, v.number, v.label, v.purpose, v.brief, time.time(), self.cfg.ask_ttl)
        self.tickets.put(t)
        self._emit(self.approver_listeners, self._ticket_view(t))
        self.log("approval_requested", call=call_id)
        return {"ok": True, "call_id": call_id, "state": job.state, "expires": t.data.ask_until}

    def status(self, call_id: str) -> dict | None:
        job = self.jobs.get(call_id)
        if not job:
            return None
        self._refresh(job)
        out = {"call_id": call_id, "state": job.state, "number": job.req.number, "label": job.req.label,
               "permit": job.permit_source}
        if job.result:
            out["result"] = job.result
        return out

    def update_brief(self, call_id: str, brief: str) -> dict:
        """The requester revises the brief while the call waits for approval.

        bind_brief on:  the old ticket is withdrawn and a new one is issued, so
                        the human always approves the brief that will be used.
        bind_brief off: the brief changes, the ticket stays (approval covers the
                        number and purpose, not the wording)."""
        job = self.jobs.get(call_id)
        if not job or job.state != "awaiting_approval":
            return {"ok": False, "error": "only a call awaiting approval can be revised"}
        v = validate({"number": job.req.number, "brief": brief, "label": job.req.label,
                      "purpose": job.req.purpose, "max_sec": job.req.max_sec})
        if isinstance(v, Invalid):
            return {"ok": False, "errors": list(v.errors)}
        job.req = v
        if self.cfg.bind_brief:
            old = self.tickets.get(call_id)
            if isinstance(old, ap.Pending):
                t = ap.open_ticket(call_id, v.number, v.label, v.purpose, v.brief, time.time(), self.cfg.ask_ttl)
                self.tickets.put(t)
                self._emit(self.approver_listeners, {**self._ticket_view(t), "revised": True})
        return {"ok": True, "state": job.state, "reissued": self.cfg.bind_brief}

    async def cancel(self, call_id: str) -> dict:
        job = self.jobs.get(call_id)
        if not job:
            return {"ok": False, "error": "no_such_call"}
        if job.state in ("awaiting_approval", "queued"):
            job.state = "cancelled"
            t = self.tickets.get(call_id)
            if isinstance(t, ap.Pending):
                self.tickets.put(ap.Expired(t.data))
            self._job_event(job)
            return {"ok": True, "state": job.state}
        if job.state == "calling" and job.line is not None:
            await job.line.hangup()
            return {"ok": True, "state": "hanging_up"}
        return {"ok": False, "error": f"cannot cancel in state {job.state}"}

    # ------------------------------------------------------------ approver side
    @staticmethod
    def _ticket_view(t: ap.Ticket) -> dict:
        d = t.data
        return {"type": "approval", "ticket_id": d.ticket_id, "state": type(t).__name__.lower(),
                "number": d.number, "label": d.label, "purpose": d.purpose, "brief": d.brief,
                "expires": d.ask_until}

    def pending(self) -> list[dict]:
        out = []
        for t in list(self.tickets.tickets.values()):
            t2 = ap.refresh(t, time.time())
            if t2 is not t:
                self.tickets.put(t2)
            if isinstance(t2, ap.Pending):
                out.append(self._ticket_view(t2))
        return out

    def decide(self, ticket_id: str, approve: bool, decided_by: str) -> dict:
        if not self.settings().allowlist_enabled:
            return {"ok": False, "error": "approval is disabled (allowlist off)"}
        t = self.tickets.get(ticket_id)
        if t is None:
            return {"ok": False, "error": "no_such_ticket"}
        now = time.time()
        t2 = ap.decide(t, approve, decided_by or "approver", now, self.cfg.use_ttl)
        changed = t2 is not t and type(t2) is not type(ap.refresh(t, now))
        self.tickets.put(t2)
        job = self.jobs.get(ticket_id)
        if isinstance(t2, ap.Approved) and job and job.state == "awaiting_approval":
            c = ap.consume(t2, job.req.number, job.req.brief, now, self.cfg.bind_brief)
            if isinstance(c, ap.Consumed):
                self.tickets.put(c)
                self._start(job, az.from_ticket(c))
            else:
                job.state = "denied"
                job.result = {"reason": c.reason}
                self._job_event(job)
        elif isinstance(t2, (ap.Denied, ap.Expired)) and job and job.state == "awaiting_approval":
            job.state = "denied" if isinstance(t2, ap.Denied) else "expired"
            self._job_event(job)
        self.log("decision", ticket=ticket_id, approve=approve, by=decided_by, state=type(t2).__name__)
        return {"ok": True, "state": type(t2).__name__.lower(), "changed": changed}

    def _refresh(self, job: CallJob) -> None:
        if job.state != "awaiting_approval":
            return
        t = self.tickets.get(job.call_id)
        t2 = ap.refresh(t, time.time()) if t else None
        if isinstance(t2, ap.Expired):
            self.tickets.put(t2)
            job.state = "expired"
            self._job_event(job)

    async def expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            for job in list(self.jobs.values()):
                self._refresh(job)

    # ------------------------------------------------------------ dialling
    def _start(self, job: CallJob, permit: az.DialPermit, **call_opts) -> None:
        job.state, job.permit_source = "queued", permit.source
        self._job_event(job, permit=permit.source)
        job.task = asyncio.create_task(self._run(job, permit, call_opts))

    async def _run(self, job: CallJob, permit: az.DialPermit, call_opts: dict) -> None:
        async with self.dial_lock:
            if job.state == "cancelled":
                return
            job.state = "calling"
            self._job_event(job)
            line = call_opts.pop("line", None) or self._make_line()
            job.line = line
            try:
                call_opts.setdefault("nudge_after", self.cfg.nudge_after)
                call_opts.setdefault("quiet_after", self.cfg.quiet_after)
                log = await place_call(self.cfg, permit, job.req, line=line, **call_opts)
                m = log.meta
                job.result = {"end_reason": m.get("end_reason"), "ended_by": m.get("ended_by"),
                              "duration_sec": m.get("duration_sec"), "record": str(log.dir),
                              "transcript": (log.dir / "transcript.txt").read_text(encoding="utf-8")}
            except Exception as e:
                job.result = {"end_reason": "error", "error": repr(e)}
            job.state = "done"
            job.line = None
            self._job_event(job, end_reason=job.result.get("end_reason"))
            with (self.cfg.data_dir / "requests.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"call_id": job.call_id, "number": job.req.number, "label": job.req.label,
                                    "purpose": job.req.purpose, "permit": job.permit_source,
                                    "end_reason": job.result.get("end_reason"), "at": time.time()},
                                   ensure_ascii=False) + "\n")

    def _make_line(self):
        from ..lines.hub import HubLine
        return HubLine(self.cfg.hub_url, self.cfg.secret(self.cfg.hub_token_file))

    # ------------------------------------------------------------ plugins
    def plugin_call(self, plugin: str, raw_request: dict, **call_opts) -> dict:
        """Entry point for authorized plugins (see plugins.py)."""
        v = validate(raw_request) if not call_opts.pop("skip_number_format", False) else None
        if isinstance(v, Invalid):
            return {"ok": False, "errors": list(v.errors)}
        if v is None:
            v = ValidatedCallRequest(raw_request["number"], raw_request["brief"], raw_request.get("label", plugin),
                                     raw_request.get("purpose", plugin), int(raw_request.get("max_sec", 300)))
        permit = az.for_plugin(self.settings(), plugin, v.number)
        if isinstance(permit, az.NotAuthorized):
            return {"ok": False, "error": permit.reason}
        call_id = time.strftime("%m%d%H%M%S-") + secrets.token_hex(3)
        job = CallJob(call_id, v, "queued")
        self.jobs[call_id] = job
        self._start(job, permit, **call_opts)
        return {"ok": True, "call_id": call_id}
