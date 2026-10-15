# landline-agent

**English** | [繁體中文](README.zh-TW.md)

**Let an AI make phone calls for you through your own landline – and you press the button that lets it dial.**

https://github.com/user-attachments/assets/831f0381-d4d5-4351-94d7-0005849f6a20

> 📞 **Only need the phone system, no AI?** Use [landline-hub](https://github.com/luna61ouo/landline-hub) on its own: calls to your landline ring your mobile, an auto-attendant routes extensions to your team, and you can dial out through the landline from your phone.

Book a table, ask about opening hours, check whether a shop has something in stock. Your AI (or any script) hands over a short brief; a voice model makes the call in natural Taiwanese Mandarin; you get the transcript and the recording back.

It runs on top of [landline-hub](https://github.com/luna61ouo/landline-hub), which owns the phone line. Voice model: Gemini Live.

## How it works

```
 your AI / script ──request──▶ landline-agent ──(approved?)──▶ landline-hub ──▶ landline ──▶ the shop
                                │      ▲
                     approval ticket   │ you press "Approve"
                                ▼      │
                          approval front-end (Discord buttons, or your own)
```

1. Your AI sends `{number, brief, label, purpose}` to the **requester API**.
2. If the number is on your allowlist, the call goes out. Otherwise a **ticket** waits for a human: who is being called, why, and the full brief the voice model will follow.
3. You approve or decline in an approval front-end. Only then does the agent ask the hub to dial – once.
4. The voice model talks to the other side. It waits for a real person before speaking (an analog line cannot tell it when someone picks up; the hub listens for that), keeps going through background noise, and hangs up when the job is done.
5. The result – how the call ended, the transcript and the recording – comes back on the same API.

## Safety by design

The AI that asks for calls and the thing that dials are two different programs with two different credentials.

- **The AI can only ask.** It holds the requester token, nothing else. Dialling happens inside the agent, and only with a dial permit – which only the approval logic (or your allowlist) can produce.
- **The AI cannot approve itself.** Approvals arrive on a separate listener (a unix socket, permission 600) with its own token. The approval front-end is a separate process. If the allowlist is switched off, that listener doesn't exist at all.
- **What you approve is what gets said.** With `bind_brief` (on by default) the brief is locked once approved; a revised brief withdraws the old ticket and asks again.
- **The voice model on the phone has no tools** to look anything up or act on anything. It can only say what is in the brief. What the other side says comes back to you as data, never as instructions.
- **Hard rules come last.** The instructions are assembled as brief → task policy → speaking style → hard rules, so nothing in a brief can override them.
- **No emergency numbers.** The agent only accepts regular phone numbers (in Taiwan: 0 followed by 7–9 digits), so 110, 119 and other short codes are rejected before anything else runs. The hub enforces the same rule a second time: the agent's role there may not dial emergency or premium numbers.
- **Every call leaves a black box**: the exact instructions the model received, a timeline, the audio as it was recorded, and a transcript.

> ⚠️ **The allowlist can be switched off** (`[allowlist] enabled = false`). Doing so also switches off approval: every requested call is dialled automatically, with no human in the loop. That is your call to make – we recommend keeping it on.

### How strong is "the AI cannot approve itself"?

It depends on how you deploy it. Be honest with yourself about which case you are in.

| Deployment | What separates the AI from approval |
|---|---|
| **The AI runs as its own OS user**, the agent as another | File permissions. The AI cannot read the approver token, the agent's hub credential, the allowlist or the config, and cannot edit the agent's code. This is the setup we recommend. |
| **The AI and the agent run as the same OS user** (typical for a personal machine) | Only whatever you put in the AI's way, e.g. a tool-call hook that refuses commands touching the agent's private folder. That stops the normal paths, not a determined one: the same user can read those files by a roundabout route, edit the agent's code and restart it, or call the hub directly with the agent's credential. |

Things worth protecting, in order of importance:

1. The **approver token** – whoever holds it can approve any call.
2. The **agent's hub credential** – whoever holds it can dial through the hub without going through approval at all.
3. The **allowlist** and the **config** (the allowlist switch lives there).
4. The **agent's code**, if the AI can write to it.

The requester token is the only thing the AI needs.

Phones paired with landline-hub are telephones: they can dial anything a normal phone can, emergency numbers included, on purpose. If your AI can also operate one of those phones (for example over adb), that path bypasses this agent entirely – block it on the AI side.

## What you need

- A running [landline-hub](https://github.com/luna61ouo/landline-hub) (or its fake gateway, for trying things out).
- Python 3.11+.
- A Gemini API key with access to a Live (native audio) model.

## Install

```bash
git clone https://github.com/luna61ouo/landline-agent && cd landline-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp config.example.toml config.toml           # set [principal].name, [hub].url, [voice]
mkdir -p data && chmod 700 data
# the Gemini key
install -m 600 /dev/stdin data/gemini-key <<< "YOUR_GEMINI_API_KEY"
# pair with the hub as an "agent" (run the first line where the hub runs)
landline-hub pair -c /path/to/hub/config.toml --name my-agent --role agent      # prints a one-time code
curl -s -d '{"code":"<code>"}' http://127.0.0.1:8910/v1/pair                     # -> {"token": ...}
install -m 600 /dev/stdin data/hub-token <<< "<token>"
landline-agent serve -c config.toml
```

On first start the agent creates `data/requester-token` (give this one to your AI) and `data/approver-token` (give this one only to your approval front-end).

### The allowlist

Numbers on the allowlist are dialled without asking. It can only be changed from the command line, not through any API:

```bash
landline-agent allow add 0212345678 "Moonlight Bistro" -c config.toml
landline-agent allow list   -c config.toml
landline-agent allow remove 0212345678 -c config.toml
```

### Approving calls

- **Discord** – `plugins/approver-discord`: a DM with the number, purpose and full brief, and *Approve* / *Decline* buttons. Runs as its own process with the approver token. See its [README](plugins/approver-discord/README.md).
- **Your own front-end** – talk to the approver socket:

| | |
|---|---|
| `GET /v1/approvals` | pending tickets (number, label, purpose, full brief, expiry) |
| `GET /v1/approvals/stream` | server-sent events: new and changed tickets |
| `POST /v1/approvals/{id}/decision` `{approve, decided_by}` | |

A ticket expires after `ask_ttl_sec` (10 minutes by default); an approved call must start within `use_ttl_sec`.

## Asking for a call

```bash
curl -H "Authorization: Bearer $(cat data/requester-token)" \
     -d '{"number":"0212345678","label":"月光小館","purpose":"訂明天晚上七點兩位",
          "brief":"【任務】訂位\n店家：月光小館\n想訂：明天晚上七點，兩位。\n如果七點沒位子，六點半到八點之間都可以。"}' \
     http://127.0.0.1:8920/v1/calls
```

| | |
|---|---|
| `POST /v1/calls` `{number, brief, label, purpose, max_sec?}` | → `{call_id, state}`: `calling` (allowlisted), or `awaiting_approval` with an expiry. Later states: `done`, `denied`, `expired`, `cancelled`. `max_sec` 30–1800, default 300. |
| `GET /v1/calls/{id}` | state; when done, `result` with `end_reason`, `ended_by`, `duration_sec`, `transcript` and the record folder |
| `GET /v1/calls/{id}/events` | server-sent events for this call |
| `PATCH /v1/calls/{id}` `{brief}` | revise while waiting for approval (with `bind_brief`, this re-issues the ticket) |
| `POST /v1/calls/{id}/cancel` | |

**Writing a brief.** Plain text, in the language of the call. A first line `【任務】訂位` picks the matching task policy from `landline_agent/policies/<locale>/` (booking is included); `【語氣】正式` switches to the formal speaking style. Put in everything the model may say – your name, the date, acceptable alternatives – because it has no way to look anything up.

## On the call

- **Opening**: the greeting is prepared while the phone rings, so the model speaks the moment a person answers.
- **Background noise**: a noise gate follows the background level and only lets through sound clearly louder than it (the black box still records the original). Music and a crowd in a busy shop don't cut the model off or keep the call open.
- **Silence**: if the other side has finished and the model stays quiet for `nudge_after_sec`, it gets a reminder. A callee who never says a word gets a goodbye after `quiet_hangup_sec`; once they have spoken, pauses of at least 30 s are allowed ("let me check…").
- **Black box**: `data/calls/<time>-<kind>-<label>/` holds `meta.json` (timing, end reason, tokens, rough cost), `events.jsonl`, `prompts/`, `audio.wav` (left = assistant, right = callee) and `transcript.txt`.

## Plugins

Plugins are Python packages exposing an entry point in the group `landline_agent.plugins`. Nothing in the request API can load or reach them, and the two powerful capabilities must be granted by name:

```toml
[plugins]
enabled = ["my-plugin"]
dial_authority = []          # may obtain dial permits on its own
session_extension = []       # may add tools, replace instructions or inject text mid-call
```

Coming: an emergency-call plugin (in development, not public yet).

## Known limitations

- **Taiwan only, for now**: number rules, prompts and the task policy are Taiwanese Mandarin (`prompts/zh-TW`), and pick-up detection in the hub is tuned to Taiwan's ringback.
- **Loud music with vocals** can still be heard as someone talking.
- Needs Gemini Live (a cloud service, billed by Google per token; `meta.json` records a rough cost per call).
- The model can be wrong. Read the transcript before you rely on a booking.

## Development

```bash
python3 -m unittest discover -s tests -t .                          # standard library only
python tools/noise_sim.py config.toml brief.md --noise crowd.wav --callee clerk.md   # a full simulated call with background noise
```

`noise_sim.py` plays the callee with a second voice model and mixes in a noise file (8 kHz mono), in real time against the real Gemini API.

## Authors

Luke Liu & Luna Liu

## License

MIT — Copyright (c) 2026 Luke Liu. See [LICENSE](LICENSE).
