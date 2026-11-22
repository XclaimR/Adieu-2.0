from app.schemas.trip_profile import ProfileUpdates, TripProfile
from app.services.grounding import ground_updates

_CORE_FIELDS = (
    "destination",
    "start_date",
    "duration_days",
    "party",
    "party_size",
    "kids",
    "budget_level",
)


class TripProfileService:

    @staticmethod
    def merge(profile: TripProfile, updates: ProfileUpdates, message: str | None = None) -> TripProfile:
        """Non-null core fields overwrite. Everything else the traveler says is a note (personality_service.py).

        With `message`, core fields the user didn't actually mention are dropped (see grounding.py).
        """
        merged = profile.model_copy(deep=True)
        if message is not None:
            updates = ground_updates(updates, message)

        for field in _CORE_FIELDS:
            value = getattr(updates, field)
            if value is not None and value != "":
                setattr(merged, field, value)
        return merged
