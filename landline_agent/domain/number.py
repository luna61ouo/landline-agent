"""Minimal number handling on the agent side.

The hub is the final authority on what can be dialled (format, emergency
numbers, per-client grants).  The agent only needs a stable normalized form
so that allowlist entries and approval tickets compare equal no matter how
the number was typed.
"""
import re

REGULAR_TW = re.compile(r"0\d{7,9}")


def normalize(raw: str, country_code: str = "886") -> str:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("00" + country_code):
        d = d[2:]
    if d.startswith(country_code):
        d = "0" + d[len(country_code):]
    return d
