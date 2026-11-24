from app.schemas.place import PickCategory, PlaceCandidate
from app.services.draft_service import DraftPlan, PlacePool, PlanDay, PlanStop

POOL = PlacePool([
    PlaceCandidate(place_id="a", name="National Pantheon", category=PickCategory.DO),
    PlaceCandidate(place_id="b", name="Lisbon Cathedral", category=PickCategory.DO),
    PlaceCandidate(place_id="c", name="Jerónimos Monastery", category=PickCategory.DO),
    PlaceCandidate(place_id="d", name="Pata Negra - Portuguese Kitchen & Steak House", category=PickCategory.EAT),
])


def plan(*stops: PlanStop) -> DraftPlan:
    return DraftPlan(title="t", summary="s", days=[PlanDay(theme="x", stops=list(stops))])


def test_note_follows_the_named_place_not_a_wrong_ref():
    # The model named the cathedral but pointed at the Pantheon's number.
    it = POOL.to_itinerary(plan(
        PlanStop(place="Lisbon Cathedral", ref=0, time_of_day="morning", note="See the cathedral"),
        PlanStop(place="Jeronimos Monastery", ref=2, time_of_day="afternoon", note="Monastery"),
    ), "Lisbon")
    assert [(a.name, a.description) for a in it.days[0].activities] == [
        ("Lisbon Cathedral", "See the cathedral"), ("Jerónimos Monastery", "Monastery")]


def test_stop_about_an_unlisted_place_is_dropped():
    it = POOL.to_itinerary(plan(
        PlanStop(place="Alfama district", ref=3, time_of_day="morning", note="Quiet walk in Alfama"),
        PlanStop(place="National Pantheon", ref=0, time_of_day="afternoon", note="Visit"),
    ), "Lisbon")
    assert [a.name for a in it.days[0].activities] == ["National Pantheon"]


def test_stops_keep_their_time_of_day_and_the_day_is_ordered():
    # Close-by places can share a morning; the timing check flags a slot that is too full.
    it = POOL.to_itinerary(plan(
        PlanStop(place="National Pantheon", ref=0, time_of_day="afternoon", note="1"),
        PlanStop(place="Lisbon Cathedral", ref=1, time_of_day="morning", note="2"),
        PlanStop(place="Pata Negra - Portuguese Kitchen & Steak House", ref=3, time_of_day="morning", note="3"),
    ), "Lisbon")
    assert [(a.name[:6], a.time_of_day) for a in it.days[0].activities] == [
        ("Lisbon", "morning"), ("Pata N", "morning"), ("Nation", "afternoon")]


def test_a_repeat_never_moves_earlier():
    it = POOL.to_itinerary(plan(
        PlanStop(place="National Pantheon", ref=0, time_of_day="evening", note="1"),
        PlanStop(place="Lisbon Cathedral", ref=1, time_of_day="evening", note="2"),
    ), "Lisbon")
    assert [a.time_of_day for a in it.days[0].activities] == ["evening", "evening"]


def test_a_draft_day_stays_at_four_stops_for_gemini():
    # Ticket #75: five nested under ten days made Gemini refuse the schema ("too many states") on every draft.
    assert PlanDay.model_json_schema()["properties"]["stops"]["maxItems"] == 4


def test_draft_days_have_no_cap_for_gemini():
    # Ticket #27: a 14-day cap with nested stops made Gemini refuse the schema (400); code trims the days instead.
    assert "maxItems" not in DraftPlan.model_json_schema()["properties"]["days"]
