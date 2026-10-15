"""Discord approval buttons for landline-agent.

A separate program, not part of the agent: it holds the approver token, the
agent's requester side never sees it.  For every pending ticket it sends the
approver a DM with the number, who, why and the full brief, plus
[Approve] [Deny].  Only the configured Discord user can press them.

  python approver_discord.py config.toml

config.toml:
  bot_token_file = "discord-bot-token"
  approver_user_id = 123456789012345678
  approver_socket = "../../data/approver.sock"
  approver_token_file = "../../data/approver-token"
  language = "zh-TW"          # or "en"
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import tomllib
from pathlib import Path

import aiohttp
import discord

log = logging.getLogger("approver-discord")
PREFIX = "lla"

TEXT = {
    "zh-TW": {"title": "📞 想打電話給「{label}」", "number": "號碼", "purpose": "用途",
              "note": "按了「同意撥打」才會撥，只撥這一通。這則 {when} 失效。", "brief": "會照這份簡報講",
              "yes": "✅ 同意撥打", "no": "❌ 不要", "only": "只有核准人可以按。",
              "approved": "✅ 同意了，現在撥。", "denied": "❌ 好，不打。", "expired_msg": "（已失效，沒有撥）",
              "already": "這張已經是「{state}」了。", "failed": "⚠️ 沒送成功（{err}），這通不會撥。",
              "revised": "（簡報改過，請看新的這張）"},
    "en": {"title": "📞 Call “{label}”?", "number": "Number", "purpose": "Purpose",
           "note": "Nothing is dialled unless you approve. One call only. Expires {when}.", "brief": "Brief",
           "yes": "✅ Approve", "no": "❌ Deny", "only": "Only the approver can do this.",
           "approved": "✅ Approved, dialling now.", "denied": "❌ Denied.", "expired_msg": "(expired, not dialled)",
           "already": "Already {state}.", "failed": "⚠️ Could not send ({err}); nothing will be dialled.",
           "revised": "(brief revised, see the new request)"},
}


class Approver(discord.Client):
    def __init__(self, conf: dict, base: Path):
        super().__init__(intents=discord.Intents.none())
        self.user_id = int(conf["approver_user_id"])
        self.socket = str((base / conf["approver_socket"]).resolve())
        self.token = (base / conf["approver_token_file"]).read_text().strip()
        self.t = TEXT[conf.get("language", "zh-TW")]
        self.sent: dict[str, discord.Message] = {}

    def _api(self):
        return aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=self.socket),
                                     headers={"Authorization": f"Bearer {self.token}"})

    async def setup_hook(self):
        self.loop.create_task(self._listen())

    async def _listen(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                async with self._api() as s, s.get("http://agent/v1/approvals/stream",
                                                   timeout=aiohttp.ClientTimeout(total=None)) as r:
                    async for raw in r.content:
                        line = raw.decode().strip()
                        if line.startswith("data:"):
                            await self._ticket(json.loads(line[5:]))
            except Exception as e:
                log.warning("approval stream: %r, retrying in 5 s", e)
            await asyncio.sleep(5)

    async def _ticket(self, t: dict):
        if t.get("type") != "approval" or t.get("state") != "pending" or time.time() > t["expires"]:
            return
        tid = t["ticket_id"]
        old = self.sent.pop(tid, None)
        if old:
            try:
                await old.edit(content=self.t["revised"], view=None, embed=None)
            except discord.HTTPException:
                pass
        user = await self.fetch_user(self.user_id)
        dm = user.dm_channel or await user.create_dm()
        exp = int(t["expires"])
        embed = discord.Embed(title=self.t["title"].format(label=t.get("label") or "?"), colour=0x2E86C1,
                              description=f"**{self.t['number']}**: {t['number']}\n**{self.t['purpose']}**: "
                                          f"{t.get('purpose') or '-'}\n\n" + self.t["note"].format(when=f"<t:{exp}:R>"))
        brief = (t.get("brief") or "").strip()
        if brief:
            embed.add_field(name=self.t["brief"], value="```\n" + brief[:950] + "\n```", inline=False)
        embed.set_footer(text=tid)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.success, label=self.t["yes"], custom_id=f"{PREFIX}|yes|{tid}"))
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label=self.t["no"], custom_id=f"{PREFIX}|no|{tid}"))
        self.sent[tid] = await dm.send(embed=embed, view=view)
        self.loop.create_task(self._expire(tid, exp))

    async def _expire(self, tid: str, exp: int):
        await asyncio.sleep(max(0.0, exp - time.time()))
        msg = self.sent.pop(tid, None)
        if msg:
            try:
                await msg.edit(content=self.t["expired_msg"], view=None)
            except discord.HTTPException:
                pass

    async def on_interaction(self, it: discord.Interaction):
        cid = (it.data or {}).get("custom_id", "")
        if not cid.startswith(PREFIX + "|"):
            return
        if it.user.id != self.user_id:
            await it.response.send_message(self.t["only"], ephemeral=True)
            return
        _, verb, tid = (cid.split("|") + ["", ""])[:3]
        try:
            async with self._api() as s, s.post(f"http://agent/v1/approvals/{tid}/decision",
                                                json={"approve": verb == "yes", "decided_by": f"discord:{it.user.id}"}) as r:
                res = await r.json(content_type=None)
        except Exception as e:
            res = {"ok": False, "error": repr(e)}
        if not res.get("ok"):
            note = self.t["failed"].format(err=res.get("error"))
        elif not res.get("changed"):
            note = self.t["already"].format(state=res.get("state"))
        else:
            note = self.t["approved"] if res.get("state") == "approved" else self.t["denied"]
        self.sent.pop(tid, None)
        await it.response.edit_message(content=note, view=None)


def main():
    logging.basicConfig(level=logging.INFO)
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml").resolve()
    conf = tomllib.loads(path.read_text(encoding="utf-8"))
    bot = Approver(conf, path.parent)
    bot.run((path.parent / conf["bot_token_file"]).read_text().strip(), log_handler=None)


if __name__ == "__main__":
    main()
