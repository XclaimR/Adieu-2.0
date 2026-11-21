"""The traveler agent's pure parts: which facts a free-text reply stated, and merging checks with the judge."""
from tests.agent.run import JUDGE_ONLY_RULE, Judgement, RuleVerdict, facts_for, merge
from tests.personas.personas import PERSONAS
from tests.personas.run import check

BACKPACKER = next(p for p in PERSONAS if p["name"] == "budget_backpacker")


def test_facts_come_from_used_sheet_answers_once_and_ignore_bad_numbers():
    used = set()
    assert facts_for(BACKPACKER, [1, 99, -1], used) == {"party": ["solo"], "party_size": [1], "kids": [False]}
    assert facts_for(BACKPACKER, [1], used) == {}
    assert used == {1}


def test_checks_decide_where_they_ran_and_the_judge_fills_the_rest():
    rules = [("no invented facts", False, "party_size=6"), ("picks fit the traveler", None, "no drafts")]
    judgement = Judgement(felt_understood=2, felt_understood_why="Made up my group.", rules=[
        RuleVerdict(rule="No invented facts", passed=True, reason="looks fine"),
        RuleVerdict(rule="picks fit the traveler", passed=True, reason="nothing odd"),
        RuleVerdict(rule=JUDGE_ONLY_RULE, passed=False, reason="asked the mood twice"),
    ])
    rows = merge(rules, judgement)
    assert [(r["rule"], r["passed"], r["judge"]) for r in rows] == [
        ("no invented facts", False, True), ("picks fit the traveler", True, True), (JUDGE_ONLY_RULE, False, False)]


def test_missing_judge_verdict_stays_unchecked():
    rows = merge([], Judgement(rules=[], felt_understood=3, felt_understood_why="ok"))
    assert rows == [{"rule": JUDGE_ONLY_RULE, "passed": None, "detail": "judged from the transcript",
                     "judge": None, "reason": "no verdict from the judge"}]


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
