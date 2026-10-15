# landline-agent

> Work in progress. Not usable yet.

Let an AI make phone calls for you through your own landline — booking a table, asking opening hours, navigating phone menus — and get back a transcript and recording.

Runs on top of landline-hub. Voice model: Gemini Live.

## Safety by design

- **The AI cannot dial by itself.** It can only *request* a call. Dialling happens inside the daemon.
- **Human approval** for numbers not on your allowlist, through a separate interface with its own credential — the AI cannot approve itself.
- The model on the phone has **no lookup tools**: it can only say what is in the brief. What the other side says comes back as data, never as instructions.
- Hard rules are always appended last; a brief cannot override them.

> ⚠️ The allowlist can be switched off. Doing so also switches off approval and every requested call is dialled automatically. Keep it on unless you understand the trade-off.

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

## Coming

- Emergency-call plugin (in development, not public yet).

## How to use

```bash
pip install -e .
cp config.example.toml config.toml
landline-hub pair -c ../hub/config.toml --name my-agent --role agent   # on the hub: prints a one-time code
curl -d '{"code":"<code>"}' http://127.0.0.1:8910/v1/pair              # -> {"token": ...}; save it to data/hub-token (chmod 600)
landline-agent serve -c config.toml
# your AI / script:
curl -H "Authorization: Bearer $(cat data/requester-token)" -d '{"number":"0212345678","brief":"...","label":"...","purpose":"..."}' http://127.0.0.1:8920/v1/calls
```

Approve from Discord with `plugins/approver-discord`, or build your own front-end on the approver socket.

## Status

- [x] Domain: approval tickets, dial permits, request validation, prompt assembly (unit tests)
- [x] Gemini Live call engine over landline-hub (verified with the fake gateway)
- [x] Requester / approver APIs, allowlist, plugin loading (verified over HTTP)
- [x] Discord approval buttons (not yet run against a live bot)
- [x] First real call through a landline (HT813) and landline-hub
- [x] A silent callee gets a goodbye and the call ends on its own (unit tests; not yet on a real call)

## License

MIT
