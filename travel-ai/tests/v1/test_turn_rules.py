import pytest

from app.schemas.trip_profile import Party, TripProfile
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction, TurnDecision
from app.services.turn_service import SHOW_PLANS, TurnService, missing_vibe

READY = TripProfile(destination="Lisbon", mood=["relaxed"], party=Party.COUPLE, duration_days=4)


def decide(action=TurnAction.ASK, **kwargs):
    return TurnDecision(action=action, reply="ok", **kwargs)


def plan(d, profile, message="hi", asked=(), streak=0, has_drafts=False, has_itinerary=False, surprise=False):
    return TurnService.plan(d, profile, message=message, asked=list(asked), questions_streak=streak,
                            has_drafts=has_drafts, has_itinerary=has_itinerary, surprise=surprise)


@pytest.mark.parametrize("profile,asked,expected", [
    (TripProfile(), [], "destination"),
    (TripProfile(destination="Lisbon"), [], "mood"),
    (TripProfile(destination="Lisbon", mood=["relaxed"]), [], "party"),
    (TripProfile(destination="Lisbon", mood=["relaxed"], party=Party.SOLO), [], "duration"),
    (TripProfile(destination="Lisbon", mood=["relaxed"], party=Party.SOLO), ["duration"], None),
    (READY, [], None),
])
def test_missing_vibe_order(profile, asked, expected):
    assert missing_vibe(profile, asked) == expected


def test_asks_for_the_missing_piece_with_default_chips_when_model_asks_something_else():
    result = plan(decide(next_question="What's your budget?"), TripProfile(destination="Lisbon"))
    assert result.action == TurnAction.ASK
    assert result.next_question == "What kind of trip are you dreaming of?"
    assert result.asked_field == "mood"


def test_keeps_the_models_wording_when_it_asks_about_the_missing_piece():
    d = decide(next_question="Who are you travelling with?", answer_options=["Solo", "Partner"])
    result = plan(d, TripProfile(destination="Lisbon", mood=["fun"]))
    assert (result.next_question, result.answer_options) == ("Who are you travelling with?", ["Solo", "Partner"])


def test_vibe_complete_offers_drafts_once():
    assert plan(decide(TurnAction.CHAT), READY).action == TurnAction.OFFER_DRAFTS
    assert plan(decide(TurnAction.CHAT), READY, has_drafts=True).action == TurnAction.CHAT


@pytest.mark.parametrize("kwargs", [
    {"message": SHOW_PLANS},
    {"streak": 4},
])
def test_stuck_signals_offer_drafts(kwargs):
    assert plan(decide(), TripProfile(destination="Lisbon"), **kwargs).action == TurnAction.OFFER_DRAFTS


def test_model_stuck_flag_offers_drafts():
    assert plan(decide(user_stuck=True), TripProfile(destination="Lisbon")).action == TurnAction.OFFER_DRAFTS


def test_stuck_without_destination_shows_destination_ideas():
    result = plan(decide(user_stuck=True), TripProfile())
    assert result.action == TurnAction.SHOW_OPTIONS
    assert result.options_request.kind == OptionsKind.DESTINATIONS


def test_options_need_an_explicit_request_before_the_vibe_is_complete():
    d = decide(TurnAction.SHOW_OPTIONS, options_request=OptionsRequest(kind=OptionsKind.STAY))
    assert plan(d, TripProfile(destination="Lisbon"), message="we like food").action == TurnAction.ASK
    assert plan(d, TripProfile(destination="Lisbon"), message="show me hotels").action == TurnAction.SHOW_OPTIONS


def test_with_an_itinerary_turns_end_with_a_change_question():
    result = plan(decide(TurnAction.CHAT), READY, has_drafts=True, has_itinerary=True)
    assert result.action == TurnAction.CHAT
    assert result.next_question == "Anything you'd like to change?"
    assert result.answer_options


def test_edit_needs_an_itinerary_and_a_request():
    d = decide(TurnAction.EDIT_ITINERARY, edit_request="more relaxed")
    assert plan(d, READY, has_drafts=True, has_itinerary=True).action == TurnAction.EDIT_ITINERARY
    # Before choosing a draft, a change request re-drafts instead.
    assert plan(d, READY, has_drafts=True).action == TurnAction.OFFER_DRAFTS


@pytest.mark.parametrize("message,expected", [
    ("surprise me, somewhere warm", True),
    ("I don't know where to go", True),
    ("Lisbon please", False),
    ("a surprise party in Rome", False),
])
def test_wants_surprise(message, expected):
    assert TurnService.wants_surprise(message) is expected


def test_surprise_always_shows_destination_ideas():
    result = plan(decide(TurnAction.CHAT), READY, surprise=True)
    assert result.action == TurnAction.SHOW_OPTIONS
    assert result.options_request.kind == OptionsKind.DESTINATIONS


def test_strip_questions_only_when_a_follow_up_exists():
    reply = "Lisbon is lovely! Are you going in May? I'll find vegetarian spots."
    assert TurnService.strip_questions(reply, "When are you going?") == \
        "Lisbon is lovely! I'll find vegetarian spots."
    assert TurnService.strip_questions(reply, None) == reply


def test_strip_questions_keeps_abbreviations_and_drops_question_examples():
    reply = "Switching to Barcelona! What style? (e.g., relaxed, cultural, etc.) I'll find spots near the beach."
    assert TurnService.strip_questions(reply, "What style?") == \
        "Switching to Barcelona! I'll find spots near the beach."
