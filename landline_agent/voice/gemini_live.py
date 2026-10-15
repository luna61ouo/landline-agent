"""Voice engine on Gemini Live: one phone call, audio in and out in real time.

Behaviour carried over from the original system, each item learned on real calls:
  - The callee answers before we speak: on analog lines the hub's answer
    detection opens the gate; until then nothing goes to the model.
  - Pre-greeting: the opening line is generated while it rings and played the
    instant the callee picks up (saves about a second).
  - Silence kick: nobody spoke for a while after pick-up -> the assistant opens.
  - Waiting for a human (transfer / hold music) mutes the assistant until the
    callee speaks for 0.3 s; prompts alone could not stop it talking to silence.
  - Optional nudge when the assistant goes quiet after a question.
  - Interruptions drop the queued speech at once.
"""
from __future__ import annotations

import asyncio

from google import genai
from google.genai import types

from ..audio import audioop
from ..domain.prompt import HANGUP_HINT  # noqa: F401  (re-exported for callers that build prompts)

IN_GAIN = 4.0               # analog lines are quiet
CALLEE_VOICE_RMS = 700
SILENCE_KICK = 2.0
RING_LIMIT = 50             # still ringing after this long = nobody answers
CWC_TRIGGER, CWC_TARGET = 10000, 6000   # sliding context window: long calls stay affordable

# Texts sent to the model (Taiwanese Mandarin, like the hard rules).
NUDGE_TEXT = "（對方剛剛問了你問題、正在等你回答。現在就回答；資料裡沒有的就說不知道。）"
PREGREET_TAIL = "（這段開場白會在對方接起來的瞬間播放，所以要一口氣講完：打招呼、說明來意、接著問第一個問題，中間不要停下來等對方。）"
KICK_TEXT = ("（電話已接通。如果剛剛是真人在講話、或一直沒人講話，現在由你打招呼、說明來意；"
             "如果聽到的是語音選單或錄音，繼續保持安靜。）")


def _decl(tool: dict) -> types.FunctionDeclaration:
    params = tool.get("parameters")
    return types.FunctionDeclaration(name=tool["name"], description=tool["description"],
                                     parameters=types.Schema(**_schema(params)) if params else None)


def _schema(d: dict) -> dict:
    out = {"type": d["type"]}
    if "properties" in d:
        out["properties"] = {k: types.Schema(**_schema(v)) for k, v in d["properties"].items()}
    if "required" in d:
        out["required"] = d["required"]
    return out


def _usage(um) -> dict:
    d = {"in": um.prompt_token_count or 0, "out": um.response_token_count or 0}
    for k, details in (("in", um.prompt_tokens_details), ("out", um.response_tokens_details)):
        for m in details or []:
            d[f"{k}_{str(m.modality).split('.')[-1].lower()}"] = m.token_count
    return d


class GeminiLive:
    def __init__(self, api_key: str, model: str, voice: str | None = None):
        self.api_key, self.model, self.voice = api_key, model, voice

    def _config(self, instructions: str, tools) -> types.LiveConnectConfig:
        speech = None
        if self.voice:
            speech = types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)))
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"], system_instruction=instructions, speech_config=speech,
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=CWC_TRIGGER, sliding_window=types.SlidingWindow(target_tokens=CWC_TARGET)),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            tools=[types.Tool(function_declarations=[_decl(t) for t in tools])],
        )

    async def run(self, line, instructions: str, log, tools, max_sec: int = 300, nudge_after: float | None = None,
                  inject: asyncio.Queue | None = None, silence_kick: float = SILENCE_KICK,
                  pregreet: bool = True, tool_handlers: dict | None = None) -> tuple[str, str]:
        """Run one connected call.  Returns (end reason, who ended it).

        inject / tool_handlers are only for operator-authorized plugins; the
        request API cannot reach them."""
        log.prompt("assistant", instructions)
        st = {"rs_in": None, "rs_out": None, "voiced_ms": 0, "last_voiced": 0.0, "waiting": False,
              "wait_voiced": 0, "ai_last": 0.0, "nudged_for": 0.0, "t0": 0.0, "held": [] if pregreet else None,
              "opened": False}

        def ai_head():
            return log.now() + line.queued_sec()

        async with genai.Client(api_key=self.api_key).aio.live.connect(
                model=self.model, config=self._config(instructions, tools)) as session:
            log.event("model_connected")

            async def kickoff():
                if pregreet:
                    log.event("kickoff", reason="pregreet")
                    await session.send_realtime_input(text=KICK_TEXT + PREGREET_TAIL)
                    return
                await line.answered.wait()
                st["t0"] = log.now()
                while st["ai_last"] == 0.0:
                    await asyncio.sleep(0.2)
                    now = log.now()
                    spoke = st["voiced_ms"] >= 200 and now - st["last_voiced"] > 0.8
                    if spoke or now - st["t0"] > silence_kick or not line.callee_talking:
                        log.event("kickoff", reason="callee_spoke" if spoke else f"silence_{silence_kick:g}s")
                        await session.send_realtime_input(text=KICK_TEXT)
                        return

            async def opener():
                """When the callee picks up, play the pre-generated greeting."""
                await line.answered.wait()
                st["opened"] = True
                log.note("system", f"callee answered ({line.answer_how})")
                if pregreet:
                    held, st["held"] = st["held"], None
                    log.event("pregreet_play", sec=round(sum(len(c) for c in held) / 16000, 2))
                    for pcm8 in held:
                        log.audio("ai", audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)[0], at=ai_head())
                        line.play(pcm8)
                        st["ai_last"] = ai_head()

            async def watchdog():
                while True:
                    await asyncio.sleep(0.2)
                    now = log.now()
                    if now > max_sec:
                        return "max_duration", "system"
                    if not line.answered.is_set() and now > RING_LIMIT:
                        log.note("system", f"no answer after {RING_LIMIT} s")
                        return "no_answer", "system"
                    if (nudge_after and not st["waiting"] and line.queued_sec() == 0
                            and st["last_voiced"] > st["ai_last"] and st["last_voiced"] != st["nudged_for"]
                            and now - st["last_voiced"] > nudge_after):
                        st["nudged_for"] = st["last_voiced"]
                        log.event("nudge", silent_sec=round(now - st["last_voiced"], 1))
                        await session.send_realtime_input(text=NUDGE_TEXT)

            async def injector():
                while True:
                    text = await inject.get()
                    log.note("system", text)
                    await session.send_realtime_input(text=text)

            async def uplink():
                while not line.ended.is_set():
                    try:
                        raw = await asyncio.wait_for(line.audio_in.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    pcm8 = audioop.mul(raw, 2, IN_GAIN)
                    if audioop.rms(pcm8, 2) > CALLEE_VOICE_RMS:
                        st["voiced_ms"] += len(pcm8) // 16
                        st["last_voiced"] = log.now()
                        if st["waiting"]:
                            st["wait_voiced"] += len(pcm8) // 16
                            if st["wait_voiced"] >= 300:
                                st["waiting"] = False
                                log.event("wait_end")
                                log.note("ai", "callee spoke, unmuted")
                    pcm16, st["rs_in"] = audioop.ratecv(pcm8, 2, 1, 8000, 16000, st["rs_in"])
                    log.audio("them", pcm16)
                    if not st["opened"] and pregreet:
                        continue      # the "hello" that opened the gate is not sent: the greeting is already playing
                    await session.send_realtime_input(audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))
                return "hangup", line.ended_by or "callee"

            async def downlink():
                while True:
                    async for m in session.receive():
                        if m.usage_metadata:
                            log.add_usage(_usage(m.usage_metadata), "ai")
                        if m.go_away:
                            log.event("go_away", time_left=m.go_away.time_left)
                        if m.tool_call:
                            for fc in m.tool_call.function_calls:
                                log.event("tool_call", name=fc.name, args=fc.args)
                                result = {"ok": True}
                                if tool_handlers and fc.name in tool_handlers:
                                    result = await tool_handlers[fc.name](fc.args or {}) or result
                                await session.send_tool_response(function_responses=[
                                    types.FunctionResponse(id=fc.id, name=fc.name, response=result)])
                                if fc.name == "wait_for_human":
                                    st["waiting"], st["wait_voiced"] = True, 0
                                    line.clear()
                                    log.note("ai", "waiting for a human (muted)")
                                    log.event("wait_start")
                                elif fc.name == "press_keys":
                                    digits = "".join(c for c in str((fc.args or {}).get("digits", "")) if c in "0123456789*#wW")
                                    line.press(digits)
                                    log.note("ai", f"pressed {digits}")
                                elif fc.name == "end_call":
                                    log.note("ai", "hung up")
                                    await asyncio.sleep(line.queued_sec() + 0.8)
                                    await line.hangup()
                                    return "hangup", "assistant"
                        sc = m.server_content
                        if not sc:
                            continue
                        if sc.interrupted:
                            log.event("interrupted", dropped_sec=round(line.queued_sec(), 2))
                            log.ai_turn_end("interrupted")
                            line.clear()
                        if sc.model_turn and not st["waiting"]:
                            for part in sc.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    pcm8, st["rs_out"] = audioop.ratecv(part.inline_data.data, 2, 1, 24000, 8000, st["rs_out"])
                                    if st["held"] is not None and not st["opened"]:
                                        st["held"].append(pcm8)
                                        continue
                                    log.audio("ai", audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)[0], at=ai_head())
                                    line.play(pcm8)
                                    st["ai_last"] = ai_head()
                        if sc.input_transcription and sc.input_transcription.text:
                            log.transcript("them", sc.input_transcription.text)
                        if sc.output_transcription and sc.output_transcription.text:
                            log.transcript("ai", sc.output_transcription.text)
                        if sc.turn_complete:
                            log.event("turn_complete")
                            log.ai_turn_end("turn_complete")

            tasks = {asyncio.create_task(uplink()), asyncio.create_task(downlink()), asyncio.create_task(watchdog())}
            aux = [asyncio.create_task(kickoff()), asyncio.create_task(opener())]
            if inject is not None:
                aux.append(asyncio.create_task(injector()))
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                result = ("unknown", "")
                for t in done:
                    if t.exception():
                        raise t.exception()
                    result = t.result() or result
                return result
            finally:
                for t in list(tasks) + aux:
                    t.cancel()
                if not line.ended.is_set():
                    await line.hangup()
