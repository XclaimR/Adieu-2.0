import pytest

from app.schemas.trip_profile import BudgetLevel, Party, ProfileUpdates
from app.services.grounding import extract_duration, extract_mood, ground_updates
from app.schemas.trip_profile import Preference, TripProfile
from app.services.trip_profile_service import TripProfileService


def test_invented_details_are_dropped():
    # What qwen3:8b returned for "I want to go to Japan".
    invented = ProfileUpdates(destination="Japan", mood=["adventurous", "culture", "foodie"], start_date="2024-04-01",
                              duration_days=7, party=Party.SOLO, party_size=1, budget_level=BudgetLevel.MID)
    grounded = ground_updates(invented, "I want to go to Japan")
    assert grounded.destination == "Japan"
    assert grounded.mood == []
    assert (grounded.start_date, grounded.duration_days, grounded.party, grounded.party_size, grounded.budget_level) == \
        (None, None, None, None, None)


def test_stated_details_are_kept():
    updates = ProfileUpdates(destination="Tokyo", mood=["relaxed"], party=Party.COUPLE, party_size=2,
                             duration_days=5, budget_level=BudgetLevel.BUDGET, start_date="May")
    grounded = ground_updates(updates, "Tokyo in May, something chill with my wife, 5 days, tight budget")
    assert grounded == updates


@pytest.mark.parametrize("chip,updates", [
    ("Just me", ProfileUpdates(party=Party.SOLO)),
    ("My partner", ProfileUpdates(party=Party.COUPLE)),
    ("Family with kids", ProfileUpdates(party=Party.FAMILY, kids=True)),
    ("Foodie", ProfileUpdates(mood=["foodie"])),
    ("Relaxed", ProfileUpdates(mood=["relaxed"])),
])
def test_chip_answers_are_kept(chip, updates):
    assert ground_updates(updates, chip) == updates


@pytest.mark.parametrize("message,days", [
    ("5 days", 5), ("4-5 days", 5), ("a week", 7), ("A weekend", 2), ("two weeks", 14),
    ("three nights in Rome", 3), ("Not sure yet", None), ("Tokyo", None),
    ("A slow week in Lisbon with my partner", 7), ("a trip next week", None),
])
def test_extract_duration(message, days):
    assert extract_duration(message) == days


def test_missed_duration_is_read_from_the_text():
    assert ground_updates(ProfileUpdates(destination="Tokyo"), "Tokyo for 5 days").duration_days == 5


@pytest.mark.parametrize("message,guess,kept", [
    ("A group of friends are going on a bachelor trip to Japan", 6, None),
    ("5 days in Japan with friends", 6, None),
    ("6 of us friends heading to Japan", 6, 6),
    ("Bachelor trip to Japan, eight friends", 8, 8),
    ("Japan with my wife", 2, 2),
])
def test_party_size_must_be_stated_or_implied(message, guess, kept):
    updates = ProfileUpdates(destination="Japan", party=Party.FRIENDS if "friends" in message else Party.COUPLE,
                             party_size=guess)
    assert ground_updates(updates, message).party_size == kept


@pytest.mark.parametrize("interests,moods", [
    ("hiking", ["adventurous", "nature"]),
    ("trekking in Nepal", ["adventurous", "nature"]),
    ("skiing", ["adventurous"]),
    ("scuba diving", ["adventurous"]),
    ("beach holiday", ["beach"]),
    ("slow food week", ["foodie"]),
    # Vague or ambiguous words must not become a mood: ask instead of guessing.
    ("fun", []),
    ("something nice", []),
    ("diving into the local food scene", []),
    ("", []),
])
def test_extract_mood(interests, moods):
    assert extract_mood(interests) == moods


HIKER = "Going solo on a hiking trip in the Dolomites for 5 days."


def _merge(message, profile=None, **updates):
    return TripProfileService.merge(profile or TripProfile(), ProfileUpdates(**updates), message)


def test_missed_mood_is_read_from_the_models_interests():
    # Issue #41: gemini sometimes returns no mood for this and files "hiking" under interests.
    merged = _merge(HIKER, destination="Dolomites", preferences=[Preference(key="interests", value="hiking")])
    assert merged.mood == ["adventurous", "nature"]


@pytest.mark.parametrize("message,preferences", [
    # The model understood the "no": beach is something to avoid, not an interest.
    ("Bali for a week, no beach please", [Preference(key="avoid", value="beach")]),
    # An interest the user never mentioned is not a mood.
    ("Dolomites for 5 days", [Preference(key="interests", value="hiking")]),
    # Raw words alone are not read: only what the model filed as an interest.
    (HIKER, []),
])
def test_no_mood_without_a_stated_interest(message, preferences):
    assert _merge(message, preferences=preferences).mood == []


def test_mood_the_user_wants_to_avoid_is_dropped():
    updates = ProfileUpdates(destination="Bali", mood=["beach", "relaxed"], preferences=[Preference(key="avoid", value="beach")])
    assert ground_updates(updates, "Bali, relaxed, but no beach please").mood == ["relaxed"]


def test_hiking_supports_a_nature_mood():
    assert ground_updates(ProfileUpdates(mood=["adventurous", "nature"]), "a hiking trip").mood == ["adventurous", "nature"]


def test_activity_interests_do_not_add_to_a_known_mood():
    merged = _merge("add a short hike on day 2", TripProfile(mood=["relaxed"]),
                    preferences=[Preference(key="interests", value="hiking")])
    assert merged.mood == ["relaxed"]
