"""Where the right to dial comes from.

A `DialPermit` is the only thing the call runner accepts.  There are exactly
four sources:

  allowlist     allowlist on, number on it (a standing, pre-approved yes)
  ticket        allowlist on, number not on it, a human approved a ticket
  auto          allowlist OFF: approval is switched off too and every call goes
                out.  This lowers safety on purpose; the operator chose it.
  plugin:<name> an installed plugin the operator explicitly authorized
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .approval import Consumed

_MINT = object()   # only this module creates permits


@dataclass(frozen=True)
class DialPermit:
    number: str
    source: str
    ticket_id: str | None = None
    _key: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._key is not _MINT:
            raise TypeError("DialPermit can only be created by landline_agent.domain.authorization")


@dataclass(frozen=True)
class Settings:
    allowlist_enabled: bool = True
    allowlist: frozenset[str] = frozenset()
    bind_brief: bool = True
    authorized_plugins: frozenset[str] = frozenset()


@dataclass(frozen=True)
class NeedsApproval:
    number: str

@dataclass(frozen=True)
class NotAuthorized:
    reason: str


def for_request(s: Settings, number: str) -> Union[DialPermit, NeedsApproval]:
    if not s.allowlist_enabled:
        return DialPermit(number, "auto", _key=_MINT)
    if number in s.allowlist:
        return DialPermit(number, "allowlist", _key=_MINT)
    return NeedsApproval(number)


def from_ticket(c: Consumed) -> DialPermit:
    return DialPermit(c.data.number, "ticket", c.data.ticket_id, _key=_MINT)


def for_plugin(s: Settings, plugin: str, number: str) -> Union[DialPermit, NotAuthorized]:
    if plugin not in s.authorized_plugins:
        return NotAuthorized(f"plugin {plugin!r} is not listed under [plugins].dial_authority")
    return DialPermit(number, f"plugin:{plugin}", _key=_MINT)


def approval_enabled(s: Settings) -> bool:
    """With the allowlist off there is nothing left to approve."""
    return s.allowlist_enabled


def startup_warnings(s: Settings) -> list[str]:
    w = []
    if not s.allowlist_enabled:
        w.append("allowlist is DISABLED: approval is off and every requested call is dialled "
                 "automatically, without any human confirming it.")
    if not s.bind_brief:
        w.append("approval.bind_brief is off: the brief may change after a human approved the call.")
    return w
