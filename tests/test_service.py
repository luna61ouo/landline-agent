"""Approval workflow, end to end inside the daemon (no real calls)."""
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from landline_agent import config as config_mod
from landline_agent.daemon import service as svc_mod
from landline_agent.domain import approval as ap

GOOD = {"number": "02-1234-5678", "brief": "問營業時間", "label": "某某餐廳", "purpose": "問今天幾點開"}


class FakeLog:
    def __init__(self, d):
        self.dir = d
        (d / "transcript.txt").write_text("ok", encoding="utf-8")
        self.meta = {"end_reason": "hangup", "ended_by": "assistant", "duration_sec": 1.0}


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "config.toml").write_text('[principal]\nname = "Alex"\n', encoding="utf-8")
        self.cfg = config_mod.load(self.tmp / "config.toml")
        self.dialled = []

        async def fake_place_call(cfg, permit, req, line=None, **kw):
            self.dialled.append((permit.number, permit.source, req.brief))
            return FakeLog(self.tmp)

        for p in (mock.patch.object(svc_mod, "place_call", fake_place_call),
                  mock.patch.object(svc_mod.AgentService, "_make_line", lambda self: object())):
            p.start()
            self.addCleanup(p.stop)

    def svc(self, **cfg_changes):
        s = svc_mod.AgentService(replace(self.cfg, **cfg_changes), log=lambda *a, **k: None)
        return s

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_invalid_request_rejected(self):
        r = self.svc().request({**GOOD, "number": "110"})
        self.assertFalse(r["ok"])

    async def test_not_on_allowlist_waits_and_nothing_is_dialled(self):
        s = self.svc()
        r = s.request(GOOD)
        self.assertEqual(r["state"], "awaiting_approval")
        await self.settle()
        self.assertEqual(self.dialled, [])
        self.assertEqual(len(s.pending()), 1)

    async def test_expiry_without_any_approver(self):
        s = self.svc(ask_ttl=0)
        r = s.request(GOOD)
        await asyncio.sleep(0.01)
        self.assertEqual(s.status(r["call_id"])["state"], "expired")
        self.assertEqual(self.dialled, [])

    async def test_approval_dials_once(self):
        s = self.svc()
        cid = s.request(GOOD)["call_id"]
        self.assertTrue(s.decide(cid, True, "human")["ok"])
        await self.settle()
        self.assertEqual(self.dialled, [("0212345678", "ticket", "問營業時間")])
        s.decide(cid, True, "human")
        await self.settle()
        self.assertEqual(len(self.dialled), 1)
        self.assertEqual(s.status(cid)["state"], "done")

    async def test_denial(self):
        s = self.svc()
        cid = s.request(GOOD)["call_id"]
        s.decide(cid, False, "human")
        await self.settle()
        self.assertEqual((self.dialled, s.status(cid)["state"]), ([], "denied"))

    async def test_allowlist_dials_without_asking(self):
        s = self.svc()
        s.allowlist.add("0212345678", "restaurant")
        r = s.request(GOOD)
        await self.settle()
        self.assertEqual((r["permit"], self.dialled[0][1]), ("allowlist", "allowlist"))

    async def test_allowlist_off_dials_everything_and_approval_is_gone(self):
        s = self.svc(allowlist_enabled=False)
        r = s.request(GOOD)
        await self.settle()
        self.assertEqual(r["permit"], "auto")
        self.assertFalse(s.decide("anything", True, "x")["ok"])
        self.assertTrue(any("DISABLED" in w for w in s.warnings()))

    async def test_revise_with_bind_brief_reissues_ticket(self):
        s = self.svc(bind_brief=True)
        cid = s.request(GOOD)["call_id"]
        s.update_brief(cid, "改問明天")
        self.assertEqual(s.pending()[0]["brief"], "改問明天")
        s.decide(cid, True, "human")
        await self.settle()
        self.assertEqual(self.dialled[0][2], "改問明天")

    async def test_revise_without_bind_brief_keeps_ticket(self):
        s = self.svc(bind_brief=False)
        cid = s.request(GOOD)["call_id"]
        s.update_brief(cid, "改問明天")
        self.assertEqual(s.pending()[0]["brief"], "問營業時間")      # what the human saw
        s.decide(cid, True, "human")
        await self.settle()
        self.assertEqual(self.dialled[0][2], "改問明天")             # what is used

    async def test_cancel_while_waiting(self):
        s = self.svc()
        cid = s.request(GOOD)["call_id"]
        await s.cancel(cid)
        self.assertEqual(s.status(cid)["state"], "cancelled")
        self.assertEqual(s.pending(), [])

    async def test_plugin_needs_dial_authority(self):
        s = self.svc()
        r = s.plugin_call("emergency", {"number": "110", "brief": "x"}, skip_number_format=True)
        self.assertFalse(r["ok"])
        s = self.svc(dial_authority=frozenset({"emergency"}))
        r = s.plugin_call("emergency", {"number": "110", "brief": "x"}, skip_number_format=True)
        await self.settle()
        self.assertTrue(r["ok"])
        self.assertEqual(self.dialled[-1][:2], ("110", "plugin:emergency"))

    async def test_tickets_survive_restart(self):
        s = self.svc()
        cid = s.request(GOOD)["call_id"]
        s2 = self.svc()
        self.assertIsInstance(s2.tickets.get(cid), ap.Pending)


if __name__ == "__main__":
    unittest.main()
