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

## Status

- [x] Pure domain: approval tickets, dial permits, request validation, prompt assembly (`python -m unittest discover -s tests -t .`)
- [ ] Call engine + hub line
- [ ] Request / approval API, plugins
- [ ] Discord approval plugin

## License

MIT
