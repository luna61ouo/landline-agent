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
  - Quiet callee: the line stays silent after the assistant finished -> it is
    told to say goodbye and hang up; still nothing a while later -> the system
    hangs up.  (Without this a silent callee kept the call open until max_sec.)
  - Interruptions drop the queued speech at once.
"""
from __future__ import annotations

import asyncio

from google import genai
from google.genai import types

from ..audio import audioop
from .noise_gate import NoiseGate
from ..domain.prompt import HANGUP_HINT  # noqa: F401  (re-exported for callers that build prompts)

IN_GAIN = 4.0               # analog lines are quiet
CALLEE_VOICE_RMS = 700
SILENCE_KICK = 2.0
RING_LIMIT = 50             # still ringing after this long = nobody answers
CWC_TRIGGER, CWC_TARGET = 10000, 6000   # sliding context window: long calls stay affordable

# Texts sent to the model (Taiwanese Mandarin, like the hard rules).
NUDGE_TEXT = "（對方剛剛問了你問題、正在等你回答。現在就回答；資料裡沒有的就說不知道。）"
PREGREET_TAIL = "（這段開場白會在對方接起來的瞬間播放，所以要一口氣講完：打招呼、說明來意、接著問第一個問題，中間不要停下來等對方。）"
QUIET_TEXT = "（對方已經好一陣子沒有出聲。請用一句話禮貌道別，然後呼叫 end_call 掛斷。）"
QUIET_GRACE = 10.0          # after the goodbye prompt, the system hangs up if the call is still open
QUIET_AFTER_TALK = 30.0     # once the callee has spoken, a pause may be "let me check" - wait much longer
KICK_TEXT = ("（電話已接通。如果剛剛是真人在講話、或一直沒人講話，現在由你打招呼、說明來意；"
             "如果聽到的是語音選單或錄音，繼續保持安靜。）")
RECALL_TEXT = ("（系統更正：剛才其實還在響鈴，對方沒有接起來，也沒聽到你說的話。"
               "現在保持安靜，不要說話，等系統通知對方接起來。）")
REANSWER_TEXT = ("（對方現在真的接起來了。剛才的話對方沒聽到：如果對方先開口就回應他，"
                 "否則現在簡短打招呼、說明來意、問第一個問題。）")

# Don't talk over the callee's "hello" (10-24: the clinic answered "廣慈您好" and the
# pre-greeting started in the same instant, both talked at once).
GREET_AFTER_QUIET = 0.6     # the callee's opening words are over after this much quiet
GREET_WAIT_INFERRED = 1.5   # pick-up only inferred: give them this long to say hello first
GREET_WAIT_MAX = 5.0        # background noise must not keep the greeting waiting forever
INFERRED = ("ringback_stopped", "no_tone", "ring_cut")


def greet_ready(now: float, t_ans: float, last_voiced: float, heard: bool, inferred: bool) -> bool:
    """Pure decision: may the pre-greeting start now?  heard = the callee has said something
    since pick-up (or was talking when the gate opened)."""
    if now - t_ans >= GREET_WAIT_MAX:
        return True
    if heard:
        return now - last_voiced >= GREET_AFTER_QUIET
    return now - t_ans >= (GREET_WAIT_INFERRED if inferred else 0.0)


MIN_HEARD_CHARS = 2        # recognised characters before we believe the callee said something


def word_chars(text: str) -> int:
    """Characters that look like words (CJK or letters); tags like <noise> do not count."""
    import re
    text = re.sub(r"<[^>]*>|\{[^}]*\}|\[[^\]]*\]", "", text)
    return sum(1 for c in text if c.isalpha())


def quiet_step(now: float, ai_last: float, last_voiced: float, prompted_at: float,
               quiet_after: float | None, *, answered: bool, speaking: bool, waiting: bool,
               callee_spoke: bool = False) -> str | None:
    """Pure decision for a silent line: None, "prompt" (ask for a goodbye) or "hangup".

    Counts from whichever was later: the end of the assistant's speech or the
    callee's last voiced audio.  quiet_after applies to a callee who never said
    a word; once they have spoken, the limit is at least QUIET_AFTER_TALK.  Never fires while the assistant is talking,
    before pick-up, or while waiting for a human on hold."""
    if not quiet_after or not answered or speaking or waiting or ai_last == 0.0:
        return None
    if prompted_at:
        if last_voiced > prompted_at:
            return None      # the callee spoke after the prompt; the conversation goes on
        return "hangup" if now - max(prompted_at, ai_last) > QUIET_GRACE else None
    limit = max(quiet_after, QUIET_AFTER_TALK) if callee_spoke else quiet_after
    return "prompt" if now - max(ai_last, last_voiced) > limit else None


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
            # background noise must not count as the callee starting to talk
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW)),
        )

    async def run(self, line, instructions: str, log, tools, max_sec: int = 300, nudge_after: float | None = None,
                  inject: asyncio.Queue | None = None, silence_kick: float = SILENCE_KICK,
                  quiet_after: float | None = None,
                  pregreet: bool = True, tool_handlers: dict | None = None) -> tuple[str, str]:
        """Run one connected call.  Returns (end reason, who ended it).

        inject / tool_handlers are only for operator-authorized plugins; the
        request API cannot reach them."""
        log.prompt("assistant", instructions)
        st = {"rs_in": None, "rs_out": None, "voiced_ms": 0, "last_voiced": 0.0, "heard_at": 0.0, "heard_chars": 0, "nudge_chars": 0, "callee_turn": False, "waiting": False,
              "wait_voiced": 0, "ai_last": 0.0, "nudged_for": 0.0, "t0": 0.0, "held": [] if pregreet else None,
              "opened": False, "quiet_at": 0.0}
        gate = NoiseGate(min_rms=CALLEE_VOICE_RMS)

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
                log.note("system", f"callee answered ({line.answer_how})")
                if pregreet:
                    t_ans, heard = log.now(), bool(line.callee_talking)
                    while True:
                        now = log.now()
                        heard = heard or st["last_voiced"] > t_ans
                        # the "answered" event arrives a moment before the callee's audio: count
                        # a callee who was talking at pick-up as voiced at least until then
                        last = max(st["last_voiced"], t_ans) if heard else st["last_voiced"]
                        if greet_ready(now, t_ans, last, heard, line.answer_how in INFERRED):
                            break
                        await asyncio.sleep(0.05)
                    log.event("greet_wait", sec=round(log.now() - t_ans, 2), heard=heard, how=line.answer_how)
                st["opened"] = True
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
                    # nudge and the silence logic go by what the recogniser heard as words, not by
                    # loudness: background voices and music are loud but rarely come out as words
                    if (nudge_after and not st["waiting"] and line.queued_sec() == 0
                            and not st["callee_turn"] and st["heard_chars"] > st["nudge_chars"]
                            and st["heard_at"] > st["ai_last"] and st["heard_at"] != st["nudged_for"]
                            and now - st["heard_at"] > nudge_after):
                        st["nudged_for"], st["nudge_chars"] = st["heard_at"], st["heard_chars"]
                        log.event("nudge", silent_sec=round(now - st["heard_at"], 1))
                        await session.send_realtime_input(text=NUDGE_TEXT)
                    q = quiet_step(now, st["ai_last"], st["heard_at"], st["quiet_at"], quiet_after,
                                   answered=line.answered.is_set(), speaking=line.queued_sec() > 0 or st["callee_turn"],
                                   waiting=st["waiting"], callee_spoke=st["heard_chars"] >= MIN_HEARD_CHARS)
                    if q == "prompt":
                        st["quiet_at"] = now
                        log.event("quiet_prompt", silent_sec=round(now - max(st["ai_last"], st["heard_at"]), 1))
                        await session.send_realtime_input(text=QUIET_TEXT)
                    elif q == "hangup":
                        log.note("system", "callee stayed silent, hanging up")
                        return "callee_silent", "system"
                    elif st["quiet_at"] and st["heard_at"] > st["quiet_at"]:
                        st["quiet_at"] = 0.0

            async def recaller():
                """10-24: the hub opened on an inference and the ring came back.  Stop talking
                into the ringback, mute until the real pick-up, then start over."""
                recalled = getattr(line, "recalled", None)
                if recalled is None:
                    return
                while True:
                    await recalled.wait()
                    recalled.clear()
                    log.event("recalled", dropped_sec=round(line.queued_sec(), 2))
                    log.note("system", "still ringing - the pick-up was misjudged; assistant muted")
                    line.clear()
                    st["waiting"], st["wait_voiced"] = True, -10**9   # only the real pick-up unmutes
                    await session.send_realtime_input(text=RECALL_TEXT)
                    await line.answered.wait()
                    if line.ended.is_set():
                        return
                    st["waiting"], st["heard_at"], st["ai_last"] = False, log.now(), log.now()
                    log.event("reanswered", how=line.answer_how)
                    log.note("system", f"callee answered ({line.answer_how})")
                    await session.send_realtime_input(text=REANSWER_TEXT)

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
                    passes = gate.feed(audioop.rms(pcm8, 2))
                    if gate.loud:
                        st["voiced_ms"] += len(pcm8) // 16
                        st["last_voiced"] = log.now()
                        if st["waiting"]:
                            st["wait_voiced"] += len(pcm8) // 16
                            if st["wait_voiced"] >= 300:
                                st["waiting"] = False
                                log.event("wait_end")
                                log.note("ai", "callee spoke, unmuted")
                    pcm16, st["rs_in"] = audioop.ratecv(pcm8, 2, 1, 8000, 16000, st["rs_in"])
                    log.audio("them", pcm16)          # the black box keeps what was really on the line
                    if not st["opened"] and pregreet:
                        continue      # the "hello" that opened the gate is not sent: the greeting is already playing
                    if not passes:
                        pcm16 = b"\0" * len(pcm16)   # background only: the model hears silence
                    await session.send_realtime_input(audio=types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))
                return "hangup", line.ended_by or "callee"

            async def downlink():
                while True:
                    async for m in session.receive():
                        if m.usage_metadata:
                            log.add_usage(_usage(m.usage_metadata), "ai")
                        if m.voice_activity and m.voice_activity.voice_activity_type:
                            # the model's own view of "the callee is talking": transcripts only arrive
                            # once a whole turn is over, far too late for the silence logic
                            start = m.voice_activity.voice_activity_type == types.VoiceActivityType.ACTIVITY_START
                            st["callee_turn"], st["heard_at"] = start, log.now()
                            log.event("callee_activity", start=start)
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
                            n = word_chars(sc.input_transcription.text)
                            st["heard_chars"] += n
                        if sc.output_transcription and sc.output_transcription.text:
                            log.transcript("ai", sc.output_transcription.text)
                        if sc.turn_complete:
                            log.event("turn_complete")
                            log.ai_turn_end("turn_complete")

            tasks = {asyncio.create_task(uplink()), asyncio.create_task(downlink()), asyncio.create_task(watchdog())}
            aux = [asyncio.create_task(kickoff()), asyncio.create_task(opener()), asyncio.create_task(recaller())]
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
