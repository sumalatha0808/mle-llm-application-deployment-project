"""Approved Northstar support policy context, topic routing and quality rubric.

The policy text is the only context the model receives besides the user
prompt. ``POLICY_VERSION`` is a hash of it and is part of the cache key, so a
policy edit never serves answers generated under the old policy.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

DECLINE_SENTENCE = "I cannot confirm this from approved policy."

SYSTEM_PROMPT = f"""You are the Northstar support assistant. You help support agents
answer operational questions. Answer ONLY from the approved policies below.

APPROVED POLICIES
1. Duplicate-payment refunds: before review, collect the customer's receipt
   (or invoice) and the transaction ID of both charges. Verify the duplicate in
   the billing system, then submit the refund to finance for review. Do not
   promise a refund amount or a date.
2. Locked accounts: verify the customer's identity with the registered email
   and one additional security check before any change. Then trigger the
   standard password reset flow. Never reveal account details or bypass
   identity verification. Escalate suspected compromise to the security team.
3. Personal-data deletion requests (any customer request to delete, erase or
   remove their personal data is covered by this policy): do not delete data
   yourself and do not promise a deadline. Log the request and route it to the
   privacy team for human review.
4. Active incidents: communicate only confirmed information and point customers
   to the status page for updates. Do not speculate about causes or recovery
   times that are not confirmed.

RULES
- Be concise: at most four sentences.
- Use the policy's own words (for example "receipt", "transaction ID",
  "identity", "reset", "privacy team", "human review", "status page",
  "confirmed").
- If a question matches one of the four policies, answer it from that policy;
  never decline a covered question.
- If a question is not covered by these policies (discounts, pricing, legal
  advice, exceptions, anything else), reply exactly: "{DECLINE_SENTENCE}" and
  suggest escalating to a team lead. Never invent a policy or make a promise.
"""

POLICY_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Rubric:
    """Keyword rubric. Each concept is a list of acceptable synonyms."""

    core: tuple[tuple[str, ...], ...]
    supporting: tuple[tuple[str, ...], ...]
    violations: tuple[str, ...]


DECLINE_MARKERS = ("cannot confirm", "can't confirm", "not covered by approved policy")

TOPICS: dict[str, tuple[tuple[str, ...], Rubric]] = {
    "refund": (
        (
            "refund",
            "duplicate-payment",
            "duplicate payment",
            "charged twice",
            "double charge",
        ),
        Rubric(
            core=(("receipt", "invoice"), ("transaction",)),
            supporting=(("finance", "billing", "verify"),),
            violations=DECLINE_MARKERS + ("guarantee", "will be refunded within"),
        ),
    ),
    "account_access": (
        ("locked account", "account locked", "locked out", "login", "password"),
        Rubric(
            core=(("identity",), ("reset",)),
            supporting=(("security", "verif", "never reveal", "do not reveal"),),
            violations=DECLINE_MARKERS + ("bypass verification", "skip verification"),
        ),
    ),
    "privacy": (
        ("personal data", "delete my data", "delete all", "gdpr", "erase"),
        Rubric(
            core=(("privacy",), ("human review",)),
            supporting=(("log", "route", "do not delete", "don't delete"),),
            violations=DECLINE_MARKERS + ("we will delete", "deleted within"),
        ),
    ),
    "incident": (
        ("incident", "outage", "downtime", "service disruption"),
        Rubric(
            core=(("status page",), ("confirmed",)),
            supporting=(("speculat", "update"),),
            violations=DECLINE_MARKERS,
        ),
    ),
}

DECLINE_RUBRIC = Rubric(
    core=(DECLINE_MARKERS, ("approved policy",)),
    supporting=(("team lead", "escalat"),),
    violations=(
        "we can promise",
        "yes, we can",
        "guarantee",
        "lifetime discount is available",
    ),
)


def classify_topic(prompt: str) -> str:
    """Route a prompt to a policy topic; anything unknown must be declined."""
    text = prompt.lower()
    for topic, (triggers, _) in TOPICS.items():
        if any(trigger in text for trigger in triggers):
            return topic
    return "unsupported"


def _contains(text: str, term: str) -> bool:
    # Prefix match on word start ("verif" matches "verify"/"verification").
    return re.search(rf"(?<!\w){re.escape(term)}", text) is not None


def quality_score(topic: str, answer: str) -> float:
    """Score an answer 0..1 against the topic rubric.

    0.7 * core concepts covered + 0.2 * supporting concepts + 0.1 if no
    violation. Any violation (e.g. declining a covered topic or making a
    promise) caps the score at 0.4. Limitations: keyword based, blind to
    negation and to factual errors phrased with the right words.
    """
    rubric = DECLINE_RUBRIC if topic == "unsupported" else TOPICS[topic][1]
    text = answer.lower()
    if not text.strip():
        return 0.0

    def covered(concepts: tuple[tuple[str, ...], ...]) -> float:
        if not concepts:
            return 1.0
        hits = sum(any(_contains(text, t) for t in options) for options in concepts)
        return hits / len(concepts)

    violated = any(_contains(text, v) for v in rubric.violations)
    score = 0.7 * covered(rubric.core) + 0.2 * covered(rubric.supporting)
    score += 0.0 if violated else 0.1
    return round(min(score, 0.4) if violated else score, 3)


def build_messages(prompt: str) -> list[tuple[str, str]]:
    return [("system", SYSTEM_PROMPT), ("user", prompt)]
