"""Build the instructions the voice model receives.  Pure string assembly.

Order matters: brief, then task policy, then speaking style, then the hard
rules LAST so nothing in a brief can override them.
"""
from __future__ import annotations

from dataclasses import dataclass

TONE_FORMAL_MARK = "【語氣】正式"
TASK_MARK = "【任務】"
HANGUP_HINT = "\n通話要結束時，說完道別就呼叫 end_call 掛電話，不要一直互道再見。"


@dataclass(frozen=True)
class PromptKit:
    """Texts for one locale.  Loaded by the outer shell from prompts/<locale>/."""
    guard: str
    style_casual: str
    style_formal: str
    policies: dict[str, str]     # task name -> policy text, e.g. {"訂位": "..."}


def task_of(brief: str) -> str | None:
    for line in brief.splitlines():
        line = line.strip()
        if line.startswith(TASK_MARK):
            return line[len(TASK_MARK):].strip() or None
    return None


def build(brief: str, kit: PromptKit, principal: str) -> str:
    style = kit.style_formal if TONE_FORMAL_MARK in brief else kit.style_casual
    task = task_of(brief)
    policy = kit.policies.get(task, "") if task else ""
    parts = [brief.strip()]
    if policy:
        parts.append(policy.strip())
    parts += [style.strip(), kit.guard.strip()]
    text = "\n\n".join(parts).replace("{principal}", principal)
    return text + "\n" + HANGUP_HINT
