import pytest

from app.schemas.trip_profile import BudgetLevel, Party, ProfileUpdates
from app.services.grounding import extract_duration, ground_updates


def test_invented_details_are_dropped():
    # What qwen3:8b returned for "I want to go to Japan".
    invented = ProfileUpdates(destination="Japan", start_date="2024-04-01",
                              duration_days=7, party=Party.SOLO, party_size=1, budget_level=BudgetLevel.MID)
    grounded = ground_updates(invented, "I want to go to Japan")
    assert grounded.destination == "Japan"
    assert (grounded.start_date, grounded.duration_days, grounded.party, grounded.party_size, grounded.budget_level) == \
        (None, None, None, None, None)


def test_stated_details_are_kept():
    updates = ProfileUpdates(destination="Tokyo", party=Party.COUPLE, party_size=2,
                             duration_days=5, budget_level=BudgetLevel.BUDGET, start_date="May")
    grounded = ground_updates(updates, "Tokyo in May, something chill with my wife, 5 days, tight budget")
    assert grounded == updates


@pytest.mark.parametrize("chip,updates", [
    ("Just me", ProfileUpdates(party=Party.SOLO)),
    ("My partner", ProfileUpdates(party=Party.COUPLE)),
    ("Family with kids", ProfileUpdates(party=Party.FAMILY, kids=True)),
])
def test_chip_answers_are_kept(chip, updates):
    assert ground_updates(updates, chip) == updates


@pytest.mark.parametrize("message,days", [
    ("5 days", 5), ("4-5 days", 5), ("a week", 7), ("A weekend", 2), ("two weeks", 14),
    ("three nights in Rome", 3), ("Not sure yet", None), ("Tokyo", None),
    ("A slow week in Lisbon with my partner", 7), ("a trip next week", None),
    ("My wife and I want a slow food week in Lisbon.", 7), ("a trip to Rome next week", None),
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


@pytest.mark.parametrize("message,party", [
    ("Bali with my sister", Party.FAMILY), ("my brother and I want Japan", Party.FAMILY),
    ("my mom recommended Bali", None), ("Bali, no kids", None),
])
def test_a_relative_coming_along_is_family(message, party):
    assert ground_updates(ProfileUpdates(destination="Bali"), message).party == party
