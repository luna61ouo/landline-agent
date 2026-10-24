import unittest

from landline_agent.voice.gemini_live import GREET_WAIT_MAX, greet_ready


class GreetWait(unittest.TestCase):
    """10-24: the pre-greeting waits for the callee's hello instead of talking over it."""

    def test_callee_talking_waits_until_quiet(self):
        self.assertFalse(greet_ready(1.0, 0.0, 0.9, True, False))      # still talking
        self.assertTrue(greet_ready(1.6, 0.0, 0.9, True, False))       # 0.7 s quiet

    def test_confirmed_silent_pickup_greets_at_once(self):
        self.assertTrue(greet_ready(0.0, 0.0, -1.0, False, False))

    def test_inferred_pickup_gives_them_time_to_say_hello(self):
        self.assertFalse(greet_ready(1.0, 0.0, -1.0, False, True))
        self.assertTrue(greet_ready(1.5, 0.0, -1.0, False, True))

    def test_noise_never_blocks_forever(self):
        self.assertTrue(greet_ready(GREET_WAIT_MAX, 0.0, GREET_WAIT_MAX - 0.01, True, True))


if __name__ == "__main__":
    unittest.main()
