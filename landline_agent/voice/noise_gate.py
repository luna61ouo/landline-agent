"""Adaptive noise gate for the callee's audio.

A fixed loudness threshold treats steady background (music in a restaurant,
kitchen noise, a crowd) as the callee talking: the speech model gets
interrupted mid-sentence, answers song lyrics, and the silence logic never
sees silence.  This gate follows the background level and only lets through
audio clearly louder than it - in practice the person holding the phone.

Pure: feed it one frame's RMS at a time, it says whether that frame is voice.
"""
from __future__ import annotations

from collections import deque


class NoiseGate:
    def __init__(self, min_rms: float = 700, ratio: float = 2.5, window: int = 150,
                 percentile: float = 0.3, hangover: int = 15, warmup: int = 25):
        """window / hangover / warmup are in frames (20 ms each).

        min_rms     absolute floor: quieter than this is never voice
        ratio       voice must be this many times louder than the background
        window      how much history the background estimate looks at (3 s)
        percentile  background = this percentile of recent frame levels, so
                    pauses between words pull it down and speech cannot raise it
        hangover    keep the gate open this long after the last loud frame,
                    so word endings are not chopped
        warmup      frames needed before the background estimate is trusted;
                    until then only very loud audio (4x min_rms) counts
        """
        self.min_rms, self.ratio, self.percentile = min_rms, ratio, percentile
        self.hangover, self.warmup = hangover, warmup
        self.hist: deque[float] = deque(maxlen=window)
        self.open_for = 0
        self.loud = False          # the last frame itself was loud (not just held open)

    @property
    def floor(self) -> float:
        if not self.hist:
            return 0.0
        s = sorted(self.hist)
        return s[int(len(s) * self.percentile)]

    def threshold(self) -> float:
        if len(self.hist) < self.warmup:
            return self.min_rms * 4
        return max(self.min_rms, self.floor * self.ratio)

    def feed(self, rms: float) -> bool:
        loud = self.loud = rms > self.threshold()
        self.hist.append(rms)
        if loud:
            self.open_for = self.hangover
            return True
        if self.open_for > 0:
            self.open_for -= 1
            return True
        return False
