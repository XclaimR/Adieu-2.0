from app.schemas.trip_profile import BudgetLevel, Party, ProfileUpdates, TripProfile
from app.services.trip_profile_service import TripProfileService


def test_core_fields_overwrite_only_when_set():
    profile = TripProfile(destination="Lisbon", duration_days=4)
    merged = TripProfileService.merge(profile, ProfileUpdates(party=Party.COUPLE, budget_level=BudgetLevel.MID))

    assert merged.destination == "Lisbon"
    assert merged.duration_days == 4
    assert merged.party == Party.COUPLE
    assert merged.budget_level == BudgetLevel.MID


def test_merge_does_not_mutate_input():
    profile = TripProfile(destination="Lisbon")
    TripProfileService.merge(profile, ProfileUpdates(destination="Porto"))
    assert profile.destination == "Lisbon"
