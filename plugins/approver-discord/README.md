# approver-discord

Approve landline-agent calls with a button in your Discord DMs.

It runs as its **own process** with the approver token; the agent's request
side never sees that token, so an AI asking for calls cannot approve itself.

1. Create a Discord application + bot, copy its token into `discord-bot-token`.
2. Invite the bot to a server you share (so it can DM you), find your user id.
3. `pip install discord.py aiohttp`
4. Copy `config.example.toml` to `config.toml`, fill in, then `python approver_discord.py config.toml`.

Only the configured user can press the buttons. A revised brief replaces the old
message, and expired requests are greyed out.
