import unittest

from landline_agent.voice.gemini_live import QUIET_AFTER_TALK, QUIET_GRACE, quiet_step


def step(now, ai_last=10.0, last_voiced=0.0, prompted_at=0.0, quiet_after=8.0, **kw):
    kw = {"answered": True, "speaking": False, "waiting": False, **kw}
    return quiet_step(now, ai_last, last_voiced, prompted_at, quiet_after, **kw)


class QuietStep(unittest.TestCase):
    def test_silent_callee_gets_goodbye_then_hangup(self):
        self.assertIsNone(step(17.9))
        self.assertEqual(step(18.1), "prompt")
        self.assertIsNone(step(25.0, prompted_at=18.1))
        self.assertEqual(step(18.1 + QUIET_GRACE + 0.1, prompted_at=18.1), "hangup")

    def test_goodbye_speech_pushes_the_hangup_back(self):
        # the assistant's goodbye ended at 24; grace counts from there
        self.assertIsNone(step(30.0, ai_last=24.0, prompted_at=18.1))
        self.assertEqual(step(24.0 + QUIET_GRACE + 0.1, ai_last=24.0, prompted_at=18.1), "hangup")

    def test_callee_speaking_after_prompt_cancels(self):
        self.assertIsNone(step(40.0, last_voiced=20.0, prompted_at=18.1, callee_spoke=True))

    def test_a_callee_who_talked_gets_a_long_pause(self):
        self.assertIsNone(step(30.0, last_voiced=12.0, callee_spoke=True))
        self.assertEqual(step(12.0 + QUIET_AFTER_TALK + 0.1, last_voiced=12.0, callee_spoke=True), "prompt")

    def test_never_fires_when_it_should_not(self):
        self.assertIsNone(step(100.0, answered=False))
        self.assertIsNone(step(100.0, speaking=True))
        self.assertIsNone(step(100.0, waiting=True))
        self.assertIsNone(step(100.0, ai_last=0.0))
        self.assertIsNone(step(100.0, quiet_after=None))


if __name__ == "__main__":
    unittest.main()
