"""Call black box: one folder per call.

calls/<YYYYMMDD-HHMMSS>-<kind>-<slug>/
  meta.json        start/end, model, end reason, duration, response latency, tokens, rough cost
  events.jsonl     timeline (t = seconds since start): connection, transcript pieces,
                   interruptions, tool calls, waits, errors with tracebacks, usage
  prompts/*.txt    the exact instructions the model received
  tracks/*.raw     audio written while the call runs (survives a crash)
  audio.wav        stereo mix after hang-up: left = assistant, right = callee
  transcript.txt   human-readable transcript with event markers
calls/index.jsonl  one summary line per call
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import traceback
import wave
from pathlib import Path

from ..audio import audioop

RATE = 16000
VOICE_RMS = 400
# Rough price, USD per million tokens (third-party summary, not checked against a bill).
PRICE_IN, PRICE_OUT = 3.0, 12.0
LABELS = {"ai": "Assistant", "them": "Callee", "system": "System"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class CallLog:
    def __init__(self, calls_dir: Path, kind: str, title: str = "", model: str = "", **extra):
        self.calls_dir = Path(calls_dir)
        self.t0 = time.monotonic()
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        slug = re.sub(r"[^\w一-鿿-]+", "-", title)[:30].strip("-") or "call"
        base = self.calls_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{slug}"
        self.dir, n = base, 1
        while True:                       # several calls in the same second must not share a folder
            try:
                self.dir.mkdir(parents=True)
                break
            except FileExistsError:
                n += 1
                self.dir = base.with_name(f"{base.name}-{n}")
        (self.dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.dir / "tracks").mkdir(exist_ok=True)
        self.meta = {"id": self.dir.name, "kind": kind, "title": title, "model": model,
                     "started_at": self.started_at, **extra}
        self._ev = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        self._tracks: dict = {}
        self._lines: list = []
        self._last_them_voice = None
        self._ai_speaking = False
        self.latencies: list[float] = []
        self.usage: dict = {}
        self.counts = {"interrupted": 0, "error": 0, "tool_call": 0}
        self.closed = False
        self._write_meta()
        self.event("start", **extra)

    def now(self) -> float:
        return time.monotonic() - self.t0

    def event(self, type_: str, **fields) -> dict:
        row = {"t": round(self.now(), 3), "type": type_, **fields}
        self._ev.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._ev.flush()
        if type_ in self.counts:
            self.counts[type_] += 1
        return row

    def prompt(self, name: str, text: str) -> None:
        (self.dir / "prompts" / f"{name}.txt").write_text(text, encoding="utf-8")
        self.event("prompt", name=name, sha=_sha(text), chars=len(text))

    def note(self, who: str, text: str) -> None:
        """Event markers also go into the transcript, so it reads in context."""
        self._lines.append([self.now(), LABELS.get(who, who), f"[{text}]"])

    def transcript(self, who: str, text: str) -> None:
        if not text:
            return
        self.event("transcript", who=who, text=text)
        label = LABELS.get(who, who)
        if self._lines and self._lines[-1][1] == label and not self._lines[-1][2].startswith("["):
            self._lines[-1][2] += text
        else:
            self._lines.append([self.now(), label, text])

    def error(self, where: str, exc: BaseException) -> None:
        self.event("error", where=where, error=repr(exc), traceback="".join(traceback.format_exception(exc)))
        self.note("system", f"error @{where}: {exc!r}")

    def audio(self, track: str, pcm16: bytes, at: float | None = None) -> None:
        """Write 16 kHz PCM16 into `track` at time `at` (default: now minus its length)."""
        if not pcm16:
            return
        dur = len(pcm16) / 2 / RATE
        if at is None:
            at = max(0.0, self.now() - dur)
        f = self._tracks.get(track)
        if f is None:
            f = self._tracks[track] = (self.dir / "tracks" / f"{track}.raw").open("w+b")
        f.seek(int(at * RATE) * 2)
        f.write(pcm16)
        loud = audioop.rms(pcm16, 2) > VOICE_RMS
        if track == "them" and loud:
            self._last_them_voice = at + dur
        if track == "ai" and loud and not self._ai_speaking:
            self._ai_speaking = True
            lat = None if self._last_them_voice is None else round(at - self._last_them_voice, 3)
            if lat is not None and 0 <= lat < 20:
                self.latencies.append(lat)
            self.event("ai_speech_start", at=round(at, 3), latency_since_them=lat)

    def ai_turn_end(self, reason: str = "turn_complete") -> None:
        if self._ai_speaking:
            self.event("ai_speech_end", reason=reason)
        self._ai_speaking = False

    def add_usage(self, d: dict, who: str = "ai") -> None:
        """d: {"in": n, "out": n, "in_audio": n, "out_audio": n, ...} from the voice adapter."""
        if not d:
            return
        self.event("usage", who=who, **d)
        u = self.usage.setdefault(who, {"in": 0, "out": 0, "in_audio": 0, "out_audio": 0})
        for k in u:
            u[k] += d.get(k, 0) or 0

    def close(self, end_reason: str, by: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        dur = self.now()
        self.event("end", reason=end_reason, by=by, duration=round(dur, 2))
        self._ev.close()
        for f in self._tracks.values():
            f.close()
        try:
            wav = self._mix()
            if wav:
                for f in (self.dir / "tracks").glob("*.raw"):
                    f.unlink()
                (self.dir / "tracks").rmdir()
        except Exception as e:
            wav = None
            self.meta["mix_error"] = repr(e)
        lat = sorted(self.latencies)
        cost_by = {w: round(u["in"] / 1e6 * PRICE_IN + u["out"] / 1e6 * PRICE_OUT, 4) for w, u in self.usage.items()}
        self.meta.update({
            "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"), "duration_sec": round(dur, 1),
            "end_reason": end_reason, "ended_by": by, "counts": self.counts,
            "latency_sec": {"n": len(lat), "median": lat[len(lat) // 2] if lat else None,
                            "max": lat[-1] if lat else None},
            "tokens": self.usage, "cost_usd_rough": round(sum(cost_by.values()), 4),
            "cost_usd_by_who": cost_by, "audio": wav,
        })
        self._write_meta()
        (self.dir / "transcript.txt").write_text(
            "\n".join(f"[{t:6.1f}s] {w}: {x.strip()}" for t, w, x in self._lines), encoding="utf-8")
        with (self.calls_dir / "index.jsonl").open("a", encoding="utf-8") as idx:
            idx.write(json.dumps({k: self.meta.get(k) for k in (
                "id", "kind", "title", "started_at", "duration_sec", "end_reason", "ended_by",
                "counts", "latency_sec", "cost_usd_rough")}, ensure_ascii=False) + "\n")

    def _mix(self):
        def read(name):
            p = self.dir / "tracks" / f"{name}.raw"
            return p.read_bytes() if p.exists() else b""
        ai, them = read("ai"), read("them")
        n = max(len(ai), len(them))
        if not n:
            return None
        ai, them = ai.ljust(n, b"\0"), them.ljust(n, b"\0")
        st = audioop.add(audioop.tostereo(ai, 2, 1, 0), audioop.tostereo(them, 2, 0, 1), 2)
        with wave.open(str(self.dir / "audio.wav"), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(st)
        return "audio.wav"

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2, default=str),
                                            encoding="utf-8")
