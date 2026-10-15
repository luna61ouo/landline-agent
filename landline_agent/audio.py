"""audioop was removed from the standard library in Python 3.13; the
`audioop-lts` package provides the same module for newer versions."""
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop  # noqa: F401

RATE = 8000
FRAME_BYTES = 320      # 20 ms of 8 kHz 16-bit mono
