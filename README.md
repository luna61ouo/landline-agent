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

## Coming

- Emergency-call plugin (in development, not public yet).

## How to use

```bash
pip install -e .
cp config.example.toml config.toml
landline-hub pair -c ../hub/config.toml --name my-agent --role agent   # on the hub; save the token to data/hub-token
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

## License

MIT
