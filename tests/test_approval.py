import unittest

from landline_agent.domain import approval as ap
from landline_agent.domain import authorization as az


def pending(now=0.0):
    return ap.open_ticket("t1", "0212345678", "Some Restaurant", "book a table", "BRIEF v1", now, ask_ttl=600)


class TicketTest(unittest.TestCase):
    def test_happy_path(self):
        t = ap.decide(pending(), True, "human", now=10, use_ttl=600)
        self.assertIsInstance(t, ap.Approved)
        c = ap.consume(t, "0212345678", "BRIEF v1", now=20, bind_brief=True)
        self.assertIsInstance(c, ap.Consumed)
        p = az.from_ticket(c)
        self.assertEqual((p.source, p.ticket_id), ("ticket", "t1"))

    def test_pending_cannot_be_consumed(self):
        r = ap.consume(pending(), "0212345678", "BRIEF v1", now=1, bind_brief=True)
        self.assertIsInstance(r, ap.Refused)

    def test_denied_and_expired(self):
        self.assertIsInstance(ap.decide(pending(), False, "human", 10, 600), ap.Denied)
        self.assertIsInstance(ap.decide(pending(), True, "human", 601, 600), ap.Expired)
        approved = ap.decide(pending(), True, "human", 10, 600)
        self.assertIsInstance(ap.refresh(approved, 611), ap.Expired)
        self.assertIsInstance(ap.consume(approved, "0212345678", "BRIEF v1", 611, True), ap.Refused)

    def test_decided_ticket_cannot_be_flipped(self):
        denied = ap.decide(pending(), False, "human", 10, 600)
        self.assertIs(ap.decide(denied, True, "someone", 11, 600), denied)

    def test_other_number_refused(self):
        t = ap.decide(pending(), True, "human", 10, 600)
        self.assertIsInstance(ap.consume(t, "0287654321", "BRIEF v1", 20, True), ap.Refused)

    def test_bind_brief_switch(self):
        t = ap.decide(pending(), True, "human", 10, 600)
        self.assertIsInstance(ap.consume(t, "0212345678", "BRIEF v2", 20, bind_brief=True), ap.Refused)
        self.assertIsInstance(ap.consume(t, "0212345678", "BRIEF v2", 20, bind_brief=False), ap.Consumed)

    def test_consumed_is_not_approved_any_more(self):
        t = ap.decide(pending(), True, "human", 10, 600)
        c = ap.consume(t, "0212345678", "BRIEF v1", 20, True)
        self.assertIsInstance(ap.consume(c, "0212345678", "BRIEF v1", 21, True), ap.Refused)


class AuthorizationTest(unittest.TestCase):
    def test_allowlist_on(self):
        s = az.Settings(allowlist_enabled=True, allowlist=frozenset({"0212345678"}))
        self.assertEqual(az.for_request(s, "0212345678").source, "allowlist")
        self.assertIsInstance(az.for_request(s, "0287654321"), az.NeedsApproval)
        self.assertTrue(az.approval_enabled(s))
        self.assertEqual(az.startup_warnings(s), [])

    def test_allowlist_off_means_everything_auto_and_no_approval(self):
        s = az.Settings(allowlist_enabled=False)
        self.assertEqual(az.for_request(s, "0287654321").source, "auto")
        self.assertFalse(az.approval_enabled(s))
        self.assertTrue(any("DISABLED" in w for w in az.startup_warnings(s)))

    def test_plugin_needs_explicit_listing(self):
        s = az.Settings()
        self.assertIsInstance(az.for_plugin(s, "emergency", "110"), az.NotAuthorized)
        s = az.Settings(authorized_plugins=frozenset({"emergency"}))
        self.assertEqual(az.for_plugin(s, "emergency", "110").source, "plugin:emergency")

    def test_permit_cannot_be_forged(self):
        with self.assertRaises(TypeError):
            az.DialPermit("0212345678", "allowlist")


if __name__ == "__main__":
    unittest.main()
