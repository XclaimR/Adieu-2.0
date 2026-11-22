"""The traveler agent's pure parts: which facts a free-text reply stated, and merging checks with the judge."""
import pytest
from pydantic import ValidationError

from tests.agent.run import (
    JUDGE_RULES, Judgement, RuleVerdict, answered, facts_for, judge_schema, merge, stored, SHOW_PLANS,
)
from tests.personas.personas import PERSONAS
from tests.personas.run import check

BACKPACKER = next(p for p in PERSONAS if p["name"] == "budget_backpacker")


def test_facts_come_from_used_sheet_answers_once_and_ignore_bad_numbers():
    used = set()
    assert facts_for(BACKPACKER, [1, 99, -1], used) == {"party": ["solo"], "party_size": [1], "kids": [False]}
    assert facts_for(BACKPACKER, [1], used) == {}
    assert used == {1}


def test_checks_decide_their_rules_and_the_judge_only_its_own():
    rules = [("no invented facts", False, "party_size=6"), ("picks fit the traveler", None, "no drafts")]
    judgement = Judgement(felt_understood=2, felt_understood_why="Made up my group.", rules=[
        RuleVerdict(rule="No invented facts", passed=True, reason="looks fine"),
        RuleVerdict(rule="picks fit the traveler", passed=True, reason="nothing odd"),
        RuleVerdict(rule="no needless questions", passed=False, reason="asked the mood twice"),
    ])
    rows = merge(rules, judgement)
    assert [(r["rule"], r["passed"], r["judge"]) for r in rows] == [
        ("no invented facts", False, True), ("picks fit the traveler", None, True), ("no needless questions", False, False),
        ("notes match what was said", None, None)]


def test_missing_judge_verdict_stays_unchecked():
    rows = merge([], Judgement(rules=[], felt_understood=3, felt_understood_why="ok"))
    assert rows == [{"rule": rule, "passed": None, "detail": "judged from the transcript",
                     "judge": None, "reason": "no verdict from the judge"} for rule in JUDGE_RULES]


def test_a_dropped_draft_fails():
    trip = {"profile": {}, "panel": None,
            "drafts": [{"title": "A", "summary": "", "itinerary": {"days": []}}] * 2}
    run = {"trip": trip, "turns": [{"drafts": 2}], "facts": BACKPACKER["facts"], "questions": 0, "plans_turn": 1,
           "vibe_due": None, "show_plans_turn": None}
    assert ("all drafts arrived", False, "2 of 3") in check(BACKPACKER, run)


def test_the_apps_default_length_is_not_invented_but_another_length_is():
    run = {"trip": {"profile": {"duration_days": 4}, "drafts": [], "panel": None}, "turns": [],
           "facts": {"destination": ["Vietnam"]}, "questions": 0, "plans_turn": None, "vibe_due": None,
           "show_plans_turn": None}
    assert check(BACKPACKER, run)[0][1] is True
    run["trip"]["profile"]["duration_days"] = 6
    assert check(BACKPACKER, run)[0][1:] == (False, "duration_days=6")


def test_an_untagged_reply_still_counts_as_the_answer_to_the_question_asked():
    assert answered(BACKPACKER, "Who's coming along?", "Just me", []) == [1]
    assert answered(BACKPACKER, "Who's coming along?", "Just me", [2]) == [2]
    assert answered(BACKPACKER, "Who's coming along?", SHOW_PLANS, []) == []


def test_the_judge_must_return_a_verdict_per_rule_by_its_exact_name():
    schema = judge_schema(["a", "b"])
    ok = {"rule": "a", "passed": True, "reason": "said it"}
    score = {"felt_understood": 4, "felt_understood_why": "ok"}
    assert schema(rules=[ok, {**ok, "rule": "b"}], **score)
    for rules in ([ok], [ok, {**ok, "rule": "c"}]):
        with pytest.raises(ValidationError):
            schema(rules=rules, **score)


def test_the_judge_and_report_see_notes_and_the_card():
    trip = {"personality": {"traits": ["Slow pace"], "notes": [
        {"target": "trip", "kind": "avoid", "text": "beach", "source": "stated"}]}}
    assert stored(trip) == ("trip avoid: beach (stated)", "Slow pace")
    assert stored({}) == ("none", "empty")
