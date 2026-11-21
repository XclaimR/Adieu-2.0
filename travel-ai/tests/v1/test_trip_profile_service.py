from app.schemas.taste import Taste, TasteUpdates
from app.schemas.trip_profile import BudgetLevel, Party, Preference, ProfileUpdates, TripProfile
from app.services.trip_profile_service import TripProfileService


def test_core_fields_overwrite_only_when_set():
    profile = TripProfile(destination="Lisbon", duration_days=4)
    merged = TripProfileService.merge(profile, ProfileUpdates(party=Party.COUPLE, budget_level=BudgetLevel.MID))

    assert merged.destination == "Lisbon"
    assert merged.duration_days == 4
    assert merged.party == Party.COUPLE
    assert merged.budget_level == BudgetLevel.MID


def test_preferences_upsert_by_normalized_key_and_remove():
    profile = TripProfile(preferences=[Preference(key="diet", value="vegetarian"), Preference(key="pace", value="slow")])
    merged = TripProfileService.merge(profile, ProfileUpdates(
        preferences=[Preference(key="Diet", value="vegan"), Preference(key="must do", value="Fado show")],
        remove_preference_keys=["pace"],
    ))

    assert {p.key: p.value for p in merged.preferences} == {"diet": "vegan", "must_do": "Fado show"}


def test_merge_does_not_mutate_input():
    profile = TripProfile(destination="Lisbon")
    TripProfileService.merge(profile, ProfileUpdates(destination="Porto"))
    assert profile.destination == "Lisbon"


def test_taste_like_cancels_dislike_and_dedupes():
    taste = Taste(likes=["Street food"], dislikes=["crowds"])
    merged = TripProfileService.merge_taste(taste, TasteUpdates(likes=["street food", "Crowds"]))

    assert merged.likes == ["Street food", "Crowds"]
    assert merged.dislikes == []


def test_preferences_that_name_core_fields_are_promoted():
    merged = TripProfileService.merge(TripProfile(destination="Lisbon"), ProfileUpdates(preferences=[
        Preference(key="budget_level", value="Budget"),
        Preference(key="budget", value="tight"),  # not a valid level: stays a preference
        Preference(key="diet", value="vegetarian"),
    ]))

    assert merged.budget_level == BudgetLevel.BUDGET
    assert {p.key: p.value for p in merged.preferences} == {"budget": "tight", "diet": "vegetarian"}


def test_preference_values_are_cleaned_of_json_debris():
    merged = TripProfileService.merge(TripProfile(), ProfileUpdates(preferences=[
        Preference(key="mobility", value="wheelchair user'}]},"),
    ]))
    assert merged.preferences[0].value == "wheelchair user"
