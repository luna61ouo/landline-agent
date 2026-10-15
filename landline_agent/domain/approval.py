"""Approval tickets: a human says yes or no to one specific call.

Each state is its own type.  `consume()` only accepts `Approved`, so
"dial with a ticket nobody approved" cannot be written.  Time is always
passed in; nothing here reads a clock.

    Pending --decide(yes)--> Approved --consume()--> Consumed
       |--decide(no)--> Denied     |--use window over--> Expired
       |--ask window over--> Expired
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Union


def brief_hash(brief: str) -> str:
    return hashlib.sha256(brief.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TicketData:
    ticket_id: str
    number: str          # normalized
    label: str           # who we are calling, shown to the approver
    purpose: str         # why, shown to the approver
    brief: str           # full brief, shown to the approver
    created: float
    ask_until: float     # approver must decide before this


@dataclass(frozen=True)
class Pending:
    data: TicketData

@dataclass(frozen=True)
class Approved:
    data: TicketData
    decided_by: str
    use_until: float     # the call must be placed before this

@dataclass(frozen=True)
class Denied:
    data: TicketData
    decided_by: str

@dataclass(frozen=True)
class Expired:
    data: TicketData

@dataclass(frozen=True)
class Consumed:
    data: TicketData
    used_at: float

Ticket = Union[Pending, Approved, Denied, Expired, Consumed]


@dataclass(frozen=True)
class Refused:
    reason: str


def open_ticket(ticket_id: str, number: str, label: str, purpose: str, brief: str,
                now: float, ask_ttl: float) -> Pending:
    return Pending(TicketData(ticket_id, number, label, purpose, brief, now, now + ask_ttl))


def refresh(t: Ticket, now: float) -> Ticket:
    """Apply the passage of time."""
    if isinstance(t, Pending) and now > t.data.ask_until:
        return Expired(t.data)
    if isinstance(t, Approved) and now > t.use_until:
        return Expired(t.data)
    return t


def decide(t: Ticket, approve: bool, decided_by: str, now: float, use_ttl: float) -> Ticket:
    """Only a still-pending ticket can be decided; anything else is returned unchanged."""
    t = refresh(t, now)
    if not isinstance(t, Pending):
        return t
    if approve:
        return Approved(t.data, decided_by, now + use_ttl)
    return Denied(t.data, decided_by)


def consume(t: Approved, number: str, brief: str, now: float, bind_brief: bool) -> Consumed | Refused:
    """Turn an approval into the right to place exactly one call."""
    if not isinstance(t, Approved):
        return Refused("ticket is not approved")
    if now > t.use_until:
        return Refused("approval expired")
    if number != t.data.number:
        return Refused("number differs from the approved one")
    if bind_brief and brief_hash(brief) != brief_hash(t.data.brief):
        return Refused("brief changed after approval")
    return Consumed(t.data, now)
