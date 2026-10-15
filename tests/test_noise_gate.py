import unittest

from landline_agent.voice.gemini_live import word_chars
from landline_agent.voice.noise_gate import NoiseGate


class Gate(unittest.TestCase):
    def test_steady_background_is_shut_out(self):
        g = NoiseGate(min_rms=700, warmup=25, hangover=15)
        first = [g.feed(3000) for _ in range(40)]          # loud but steady: music, a crowd
        later = [g.feed(3000) for _ in range(200)]
        self.assertLessEqual(sum(first), 40)               # start-up: the background is not known yet
        self.assertFalse(any(later))                       # once it is, the gate stays shut

    def test_a_voice_over_the_background_gets_through(self):
        g = NoiseGate(min_rms=700)
        for _ in range(100):
            g.feed(2000)
        self.assertTrue(g.feed(9000))
        self.assertTrue(g.loud)

    def test_word_endings_are_held_open(self):
        g = NoiseGate(min_rms=700, hangover=5)
        for _ in range(100):
            g.feed(500)
        g.feed(9000)
        held = [g.feed(500) for _ in range(7)]
        self.assertEqual(held, [True] * 5 + [False] * 2)
        self.assertFalse(g.loud)

    def test_quiet_line_never_opens(self):
        g = NoiseGate(min_rms=700)
        self.assertFalse(any(g.feed(300) for _ in range(300)))

    def test_until_warmed_up_only_very_loud_audio_counts(self):
        g = NoiseGate(min_rms=700, warmup=25)
        self.assertFalse(g.feed(2000))
        self.assertTrue(g.feed(3000))


class WordChars(unittest.TestCase):
    def test_counts_words_not_tags(self):
        self.assertEqual(word_chars("好的"), 2)
        self.assertEqual(word_chars("<noise>{pause}[music]"), 0)
        self.assertEqual(word_chars("ok, 7 點"), 3)


if __name__ == "__main__":
    unittest.main()
