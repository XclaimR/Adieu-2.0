import re

from pydantic import ValidationError

from app.schemas.taste import Taste, TasteUpdates
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

MAX_TASTE_ITEMS = 30

# Preference keys models sometimes use for core fields.
_CORE_ALIASES = {"budget": "budget_level", "travel_party": "party", "duration": "duration_days", "days": "duration_days"}


def _promote_core_preferences(updates: ProfileUpdates) -> ProfileUpdates:
    """Move preferences like {key: "budget_level", value: "budget"} into the matching core field."""
    updates = updates.model_copy(deep=True)
    remaining = []
    for pref in updates.preferences:
        field = _CORE_ALIASES.get(_norm(pref.key), _norm(pref.key))
        if field in _CORE_FIELDS and getattr(updates, field) is None:
            try:
                validated = ProfileUpdates.model_validate({field: pref.value.strip().lower()})
            except ValidationError:
                remaining.append(pref)
                continue
            setattr(updates, field, getattr(validated, field))
        else:
            remaining.append(pref)
    updates.preferences = remaining
    return updates


def _clean_value(value: str) -> str:
    """Small models sometimes leak JSON punctuation into strings (e.g. "wheelchair user'}]},")."""
    return re.sub(r"[\[\]{}\"]+", "", value.split("',")[0]).strip(" '\t\n,.:;")[:80]


def _norm(key: str) -> str:
    return key.strip().lower().replace(" ", "_")


class TripProfileService:

    @staticmethod
    def merge(profile: TripProfile, updates: ProfileUpdates, message: str | None = None) -> TripProfile:
        """Non-null core fields overwrite; preferences upsert by key; removals drop by key.

        With `message`, core fields the user didn't actually mention are dropped (see grounding.py).
        """
        merged = profile.model_copy(deep=True)
        updates = _promote_core_preferences(updates)
        if message is not None:
            updates = ground_updates(updates, message)

        for field in _CORE_FIELDS:
            value = getattr(updates, field)
            if value is not None and value != "":
                setattr(merged, field, value)

        moods = {m.strip().lower(): None for m in merged.mood + updates.mood if m.strip()}
        merged.mood = list(moods)

        removed = {_norm(k) for k in updates.remove_preference_keys}
        by_key = {_norm(p.key): p for p in merged.preferences if _norm(p.key) not in removed}
        for pref in updates.preferences:
            value = _clean_value(pref.value)
            if pref.key.strip() and value:
                pref = pref.model_copy(update={"key": _norm(pref.key), "value": value})
                by_key[pref.key] = pref
        merged.preferences = list(by_key.values())

        return merged

    @staticmethod
    def merge_taste(taste: Taste, updates: TasteUpdates) -> Taste:
        """Append new likes/dislikes (case-insensitive dedupe); a new like cancels a matching dislike and vice versa."""
        likes = list(taste.likes)
        dislikes = list(taste.dislikes)

        def add(target: list[str], other: list[str], item: str):
            item = item.strip()
            if not item:
                return
            low = item.lower()
            other[:] = [o for o in other if o.lower() != low]
            if low not in (t.lower() for t in target):
                target.append(item)

        for like in updates.likes:
            add(likes, dislikes, like)
        for dislike in updates.dislikes:
            add(dislikes, likes, dislike)

        return taste.model_copy(update={
            "likes": likes[-MAX_TASTE_ITEMS:],
            "dislikes": dislikes[-MAX_TASTE_ITEMS:],
        })
