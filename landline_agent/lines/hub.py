"""A phone line provided by landline-hub.

The agent is just another client of the hub: it asks for the line, gets the
audio over a WebSocket and hangs up.  It never talks to the gateway itself.
"""
from __future__ import annotations

import asyncio
import json
import time

import aiohttp

from ..domain.authorization import DialPermit

KEY_TONE_SEC = 0.25     # one key press as the hub plays it (150 ms tone + 100 ms gap)


class HubLine:
    def __init__(self, url: str, token: str, log=None, connect_timeout: float = 60):
        self.url, self.token = url.rstrip("/"), token
        self.log = log or (lambda *a, **k: None)
        self.connect_timeout = connect_timeout
        self.audio_in: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self.ended = asyncio.Event()
        self.ended_by: str | None = None
        self.fail_reason: str | None = None
        self.answered = asyncio.Event()      # the callee picked up (as far as the hub can tell)
        self.answer_how = ""
        self.recalled = asyncio.Event()      # 10-24: the hub took "answered" back (it was still ringing)
        self.callee_talking = False
        self.call_id: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws = None
        self._out: asyncio.Queue = asyncio.Queue()
        self._play_end = 0.0
        self._tasks: list[asyncio.Task] = []
        self._connected = asyncio.Event()

    @property
    def _h(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def dial(self, permit: DialPermit) -> bool:
        if not isinstance(permit, DialPermit):
            raise TypeError("dial() needs a DialPermit")
        self._session = aiohttp.ClientSession()
        async with self._session.post(f"{self.url}/v1/outbound", headers=self._h,
                                      json={"number": permit.number, "answer_detection": True}) as r:
            res = await r.json()
        if not res.get("ok"):
            self.fail_reason = res.get("error", f"http_{r.status}")
            await self._close()
            return False
        self.call_id = res["call_id"]
        self._ws = await self._session.ws_connect(f"{self.url}/v1/calls/{self.call_id}/media", headers=self._h,
                                                  heartbeat=15, max_msg_size=1 << 20)
        self._tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._writer())]
        done, _ = await asyncio.wait([asyncio.create_task(self._connected.wait()),
                                      asyncio.create_task(self.ended.wait())],
                                     timeout=self.connect_timeout, return_when=asyncio.FIRST_COMPLETED)
        if self._connected.is_set():
            return True
        if not self.ended.is_set():
            self.fail_reason = "connect_timeout"
            await self.hangup()
        return False

    async def _reader(self) -> None:
        try:
            async for m in self._ws:
                if m.type == aiohttp.WSMsgType.BINARY:
                    try:
                        self.audio_in.put_nowait(m.data)
                    except asyncio.QueueFull:
                        pass
                elif m.type == aiohttp.WSMsgType.TEXT:
                    ev = json.loads(m.data)
                    t = ev.get("type")
                    self.log("hub_event", **{k: v for k, v in ev.items() if k not in ("call_id", "at")})
                    if t == "line_connected":
                        self._connected.set()
                    elif t == "callee_answered":
                        self.answer_how = ev.get("how", "")
                        self.callee_talking = bool(ev.get("talking"))
                        self.answered.set()
                    elif t == "callee_not_answered":
                        self.answered.clear()
                        self.answer_how = ""
                        self.recalled.set()
                    elif t == "ended":
                        reason = ev.get("reason", "")
                        if reason.startswith("not_connected"):
                            self.fail_reason = reason.split(":", 1)[-1]
                        self.ended_by = {"remote": "callee", "local": "assistant"}.get(ev.get("by") or "", ev.get("by") or "system")
                        break
        finally:
            self.ended.set()

    async def _writer(self) -> None:
        while True:
            kind, data = await self._out.get()
            if kind == "audio":
                await self._ws.send_bytes(data)
            else:
                await self._ws.send_str(json.dumps(data))

    # -- line interface used by the voice engine --
    def play(self, pcm8: bytes) -> None:
        now = time.monotonic()
        self._play_end = max(now, self._play_end) + len(pcm8) / 16000
        self._out.put_nowait(("audio", pcm8))

    def clear(self) -> None:
        self._play_end = 0.0
        while not self._out.empty():
            self._out.get_nowait()
        self._out.put_nowait(("ctl", {"type": "clear"}))

    def queued_sec(self) -> float:
        return max(0.0, self._play_end - time.monotonic())

    def press(self, digits: str) -> None:
        digits = "".join(c for c in digits if c in "0123456789*#wW")
        now = time.monotonic()
        self._play_end = max(now, self._play_end) + KEY_TONE_SEC * len(digits)
        self._out.put_nowait(("ctl", {"type": "dtmf", "digits": digits}))

    async def hangup(self) -> None:
        if self._ws is not None and not self._ws.closed and not self.ended.is_set():
            self.ended_by = self.ended_by or "assistant"
            await self._ws.send_str(json.dumps({"type": "hangup"}))
            try:
                await asyncio.wait_for(self.ended.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        self.ended.set()
        await self._close()

    async def close(self) -> None:
        """Release network resources (safe to call more than once)."""
        await self._close()

    async def _close(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()
