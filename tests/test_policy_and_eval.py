"""Policy routing, quality rubric and the shared-case evaluation."""

import asyncio

import pytest

from src.evaluation import evaluate_case, load_cases
from src.policy import (
    DECLINE_SENTENCE,
    SYSTEM_PROMPT,
    classify_topic,
    quality_score,
)
from src.provider import DRY_ANSWERS, DryProvider
from src.service import GenerationService
from tests.helpers import make_settings


@pytest.mark.parametrize(
    ("case_id", "topic"),
    [
        ("refund_supported", "refund"),
        ("account_access", "account_access"),
        ("privacy_handoff", "privacy"),
        ("incident_status", "incident"),
        ("unsupported_policy", "unsupported"),
    ],
)
def test_shared_cases_route_to_expected_topic(case_id, topic):
    case = next(c for c in load_cases() if c.case_id == case_id)
    assert classify_topic(case.prompt) == topic


def test_policy_answers_score_high_and_wrong_behaviour_scores_low():
    for topic, answer in DRY_ANSWERS.items():
        assert quality_score(topic, answer) >= 0.9, topic
    assert quality_score("refund", DECLINE_SENTENCE) <= 0.4  # declined a covered topic
    assert quality_score("unsupported", "Yes, we can promise that discount.") <= 0.4
    assert quality_score("incident", "") == 0.0


def test_system_prompt_states_decline_rule():
    assert DECLINE_SENTENCE in SYSTEM_PROMPT


def test_all_shared_cases_pass_with_dry_provider(tmp_path):
    service = GenerationService(DryProvider(), make_settings(tmp_path))

    async def run_all():
        return [await evaluate_case(case, service) for case in load_cases()]

    results = asyncio.run(run_all())
    assert len(results) == 5
    assert all(r.terms_passed and r.errors == 0 for r in results)
    refund = next(r for r in results if r.case_id == "refund_supported")
    assert refund.cache_evidence.startswith("verified") and refund.provider_calls == 1
    assert all(r.quality_mean >= 0.8 for r in results)
