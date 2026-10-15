"""Development only: run one call against a simulated callee with background noise.

No phone line, no hub: the assistant talks to a stand-in line that mixes a noise
recording into what the callee "says".  The callee is either silent (noise only)
or a second Gemini Live session playing a role from a brief.

  python tools/noise_sim.py config.toml brief.md --noise music.wav [--gain 1.0]
                            [--callee callee_brief.md] [--max-sec 90] [--ring 3]

Everything is real time (a 60 s call takes 60 s).  The call record lands in the
config's data_dir/calls like a real call, so the usual black box applies.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from landline_agent import config as config_mod  # noqa: E402
from landline_agent.audio import audioop  # noqa: E402
from landline_agent.domain import authorization as az  # noqa: E402
from landline_agent.domain.call_request import ValidatedCallRequest, validate  # noqa: E402
from landline_agent.engine.call import place_call  # noqa: E402

FRAME = 160            # 20 ms at 8 kHz
FRAME_SEC = 0.02


def load_pcm8(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 8000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit(f"{path}: needs 8 kHz mono 16-bit (ffmpeg -ac 1 -ar 8000 -sample_fmt s16)")
        return w.readframes(w.getnframes())


class SimLine:
    """Same surface as HubLine, wired to a simulated callee."""

    def __init__(self, noise: bytes, gain: float, ring_sec: float):
        self.ended = asyncio.Event()
        self.answered = asyncio.Event()
        self.answer_how = "sim"
        self.callee_talking = False
        self.ended_by = ""
        self.fail_reason = ""
        self.audio_in: asyncio.Queue = asyncio.Queue()
        self.to_callee: asyncio.Queue = asyncio.Queue()   # what the assistant says, paced by the line
        self.callee_out = bytearray()                     # what the simulated person says (8 kHz)
        self.noise, self.gain, self.ring_sec = noise, gain, ring_sec
        self._play_end = 0.0
        self._out = bytearray()
        self._tasks = []

    async def dial(self, permit) -> bool:
        self._tasks = [asyncio.create_task(self._run())]
        return True

    async def _run(self):
        t0 = time.monotonic()
        n, pos = 0, 0
        while not self.ended.is_set():
            n += 1
            await asyncio.sleep(max(0.0, t0 + n * FRAME_SEC - time.monotonic()))
            if not self.answered.is_set():
                if time.monotonic() - t0 >= self.ring_sec:
                    self.answered.set()
                continue
            nz = self.noise[pos * 2:(pos + FRAME) * 2]
            if len(nz) < FRAME * 2:
                pos, nz = 0, self.noise[:FRAME * 2]
            pos += FRAME
            frame = audioop.mul(nz, 2, self.gain)
            if self.callee_out:
                voice = bytes(self.callee_out[:FRAME * 2]).ljust(FRAME * 2, b"\0")
                del self.callee_out[:FRAME * 2]
                frame = audioop.add(frame, voice, 2)
            self.audio_in.put_nowait(frame)
            # assistant audio reaches the callee in real time; silence in between keeps the
            # stream going, otherwise the callee's model never hears the turn end
            chunk = bytes(self._out[:FRAME * 2]).ljust(FRAME * 2, b"\0")
            del self._out[:FRAME * 2]
            self.to_callee.put_nowait(chunk)

    def play(self, pcm8: bytes) -> None:
        now = time.monotonic()
        self._play_end = max(now, self._play_end) + len(pcm8) / 16000
        self._out.extend(pcm8)

    def clear(self) -> None:
        self._play_end = 0.0
        self._out.clear()

    def queued_sec(self) -> float:
        return max(0.0, self._play_end - time.monotonic())

    def press(self, digits: str) -> None:
        pass

    async def hangup(self, by: str = "assistant") -> None:
        if not self.ended.is_set():
            self.ended_by = self.ended_by or by
            self.ended.set()

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()


async def callee(line: SimLine, api_key: str, model: str, brief: str) -> None:
    """A second Gemini Live session playing the person who picked up."""
    try:
        await _callee(line, api_key, model, brief)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[sim] callee failed: {e!r}", file=sys.stderr, flush=True)


async def _callee(line: SimLine, api_key: str, model: str, brief: str) -> None:
    end_tool = types.Tool(function_declarations=[types.FunctionDeclaration(
        name="end_call", description="掛斷電話。對話結束、對方道別後呼叫。")])
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"], system_instruction=brief, tools=[end_tool],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck"))))
    await line.answered.wait()
    async with genai.Client(api_key=api_key).aio.live.connect(model=model, config=cfg) as s:
        await s.send_realtime_input(text="（電話響了，你接起來。用你的開場白接聽。）")

        async def uplink():
            rs = None
            while not line.ended.is_set():
                pcm8 = await line.to_callee.get()
                pcm16, rs = audioop.ratecv(pcm8, 2, 1, 8000, 16000, rs)
                await s.send_realtime_input(audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))

        async def downlink():
            rs = None
            while not line.ended.is_set():
                async for m in s.receive():
                    if m.tool_call:
                        for fc in m.tool_call.function_calls:
                            if fc.name == "end_call":
                                await asyncio.sleep(len(line.callee_out) / 16000 + 0.5)
                                await line.hangup("callee")
                                return
                    sc = m.server_content
                    if sc and sc.interrupted:
                        line.callee_out.clear()
                    if sc and sc.model_turn:
                        for p in sc.model_turn.parts:
                            if p.inline_data and p.inline_data.data:
                                pcm8, rs = audioop.ratecv(p.inline_data.data, 2, 1, 24000, 8000, rs)
                                line.callee_out.extend(pcm8)

        tasks = [asyncio.create_task(uplink()), asyncio.create_task(downlink()),
                 asyncio.create_task(line.ended.wait())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("brief")
    ap.add_argument("--noise", required=True)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--callee", help="brief for a simulated person; omit for a callee who never speaks")
    ap.add_argument("--max-sec", type=int, default=90)
    ap.add_argument("--ring", type=float, default=3.0)
    a = ap.parse_args()
    cfg = config_mod.load(a.config)
    req = validate({"number": "0212345678", "brief": Path(a.brief).read_text(encoding="utf-8"),
                    "label": f"sim {Path(a.noise).stem}", "purpose": "noise simulation", "max_sec": a.max_sec})
    if not isinstance(req, ValidatedCallRequest):
        sys.exit(f"invalid: {req.errors}")
    permit = az.for_request(az.Settings(allowlist=frozenset({req.number})), req.number)
    line = SimLine(load_pcm8(Path(a.noise)), a.gain, a.ring)
    jobs = []
    if a.callee:
        jobs.append(asyncio.create_task(callee(line, cfg.secret(cfg.voice_key_file), cfg.voice_model,
                                               Path(a.callee).read_text(encoding="utf-8"))))
    log = await place_call(cfg, permit, req, line=line,
                           nudge_after=cfg.nudge_after, quiet_after=cfg.quiet_after)
    for j in jobs:
        j.cancel()
    print((log.dir / "transcript.txt").read_text(encoding="utf-8"))
    m = log.meta
    print(f"\n{m['duration_sec']} s | {m['end_reason']}/{m['ended_by']} | {log.dir}")


if __name__ == "__main__":
    asyncio.run(main())
