import pytest

from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Note, NoteChange
from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.trip_profile import BudgetLevel, Party, TripProfile
from app.schemas.turn import TurnAction, TurnDecision
from app.services.draft_service import DraftPlan, DraftService, PlacePool, PlanDay, PlanEdit, PlanStop, fulfilled
from app.services.personality_service import PersonalityService, check_changes
from app.services.places_service import traveler_fit
from app.services.ranking_service import Ranking, RankedItem, RankingService
from app.services.llm_client import LLMClient
from app.services.turn_service import TurnService, decision_schema
from tests.v1.test_api import new_trip, ready_trip, say


def add(text, quote=None, **kw):
    return NoteChange(op="add", text=text, quote=quote or text, **kw)


def update(*changes, stored=None, profile=TripProfile()):
    return PersonalityService.update(stored, profile, changes)


def plan():
    return Itinerary(destination="Tokyo", stay=Pick(place_id="palace", name="Palace", category="stay", tier="luxury"),
                     days=[DayPlan(day_number=1, activities=[
                               Activity(id="i1", name="Temple", category="do", time_of_day="morning", place_id="p1")]),
                           DayPlan(day_number=2, activities=[
                               Activity(id="i2", name="Museum", category="do", time_of_day="morning", place_id="p2"),
                               Activity(id="i3", name="Narisawa", category="eat", time_of_day="evening", place_id="p3")])])


def test_a_second_avoid_never_erases_the_first():
    p = update(add("beach", kind="avoid"))
    p = update(add("nightlife", kind="avoid"), stored=p)
    assert [n.text for n in p.notes] == ["beach", "nightlife"]
    assert [n.text for n in update(add("beach", kind="avoid"), add("nightlife", kind="avoid")).notes] == \
        ["beach", "nightlife"]


def test_taking_a_note_back_removes_only_that_note():
    p = update(add("beach", kind="avoid"), add("nightlife", kind="avoid"))
    back = NoteChange(op="remove", id=p.notes[0].id, text="beach is fine", quote="beach is fine")
    assert [n.text for n in update(back, stored=p).notes] == ["nightlife"]


def test_a_one_time_instruction_stores_nothing_and_keeps_the_notes_on_that_slot():
    p = update(add("birthday dinner", target="i3"))
    do = NoteChange(op="do", id=p.notes[0].id, target="i3", text="change restaurant", quote="change that restaurant")
    assert update(do, stored=p).notes == p.notes


def test_only_real_quotes_and_existing_note_ids_are_kept():
    stored = update(add("beach", kind="avoid"))
    changes = [add("slow", "really slow"), add("party", "party every night"), add("art", "art"),
               NoteChange(op="remove", id="nope", text="beach", quote="beach")]
    kept = check_changes(changes, "Really slow, partying at the beach", stored, None)
    assert [c.text for c in kept] == ["slow"]


@pytest.mark.parametrize("message,quote", [
    ("Sin museos, por favor", "sin museos"),
    ("美術館は行きたくない", "美術館"),
    ("I don’t want crowds", "don't want crowds"),
])
def test_quotes_work_in_any_language(message, quote):
    assert check_changes([add("a wish", quote)], message, None, None)


def test_targets_must_exist_and_a_stay_wish_for_a_day_means_the_stay():
    cheaper = add("cheaper", target="d1", category="stay", then="change")
    assert check_changes([cheaper], "cheaper", None, plan())[0].target == "s1"
    unknown = check_changes([add("sushi", target="i9", then="change")], "sushi", None, plan())[0]
    assert (unknown.target, unknown.then) == ("trip", "none")  # never a whole-trip edit by accident


def test_the_models_schema_only_allows_ids_on_this_trip():
    schema = decision_schema(plan())
    change = {"op": "add", "text": "x", "quote": "x", "target": "i2"}
    assert schema.model_validate({"action": "chat", "reply": "ok", "note_changes": [change]})
    with pytest.raises(ValueError):
        schema.model_validate({"action": "chat", "reply": "ok", "note_changes": [{**change, "target": "i9"}]})


def test_taking_a_note_back_never_edits_the_plan():
    back = NoteChange(op="remove", id="n1", text="beach", quote="beach is fine", then="change")
    d = TurnDecision(action=TurnAction.EDIT_ITINERARY, reply="ok", note_changes=[back], edit_request="beach is fine")
    ready = TripProfile(destination="Bali", mood=["relaxed"], party=Party.COUPLE, duration_days=3)
    turn = TurnService.plan(d, ready, message="beach is fine", asked=[], questions_streak=0, has_drafts=True,
                            has_itinerary=True)
    assert turn.action == TurnAction.CHAT


def test_an_edit_the_model_forgot_to_mark_still_changes_its_target():
    d = TurnDecision(action=TurnAction.EDIT_ITINERARY, reply="ok", note_changes=[add("more relaxed", target="d2")])
    assert [c.target for c in TurnService.to_change(d)] == ["d2"]


def test_mood_follows_stated_notes_only():
    before = update()
    after = update(add("hiking trip", mood="adventurous"), add("early nights", source="inferred", mood="relaxed"))
    moods = PersonalityService.moods([], before, after)
    assert moods == ["adventurous"]
    back = NoteChange(op="remove", id=after.notes[0].id, text="hiking", quote="no hiking")
    assert PersonalityService.moods(moods, after, update(back, stored=after)) == []


def test_the_card_is_derived_from_trip_wide_notes():
    slow = add("slow", toward="slow", strong=True)
    night = add("one big night out", toward="nightlife", once=True)
    p = update(slow, night)
    assert PersonalityService.card(p).traits == ["Slow pace (strongly)"]
    assert PersonalityService.moments(p) == ["one big night out"]
    back = NoteChange(op="remove", id=p.notes[0].id, text="slow", quote="forget slow")
    assert PersonalityService.card(update(back, stored=p)).traits == []


def test_inferred_is_a_guess_and_never_beats_a_known_fact():
    guess = add("out late", toward="nightlife", source="inferred")
    assert PersonalityService.traits(update(guess)) == ([], ["Nightlife"])
    family = TripProfile(party=Party.FAMILY, budget_level=BudgetLevel.MID)
    assert PersonalityService.card(update(guess, profile=family)).traits == ["Chill evenings", "Mid-range comfort"]


def test_prompts_separate_hard_limits_wishes_and_moments():
    p = update(add("beach", kind="avoid"), add("slow"), add("cooking class", once=True),
               add("bars", kind="avoid", source="inferred"))
    limits, wishes, moments = [line for line in traveler_fit(TripProfile(), p).split("\n") if "personality" not in line]
    assert "avoid: beach" in limits and "bars" not in limits  # a guess never removes places
    assert "slow" in wishes and "bars, inferred" in wishes
    assert "[m0] 'cooking class'" in moments


def test_each_note_is_shown_under_what_it_is_about():
    p = update(add("nothing early", target="d2"), add("birthday", target="i3"), add("slow"))
    view = PersonalityService.plan_view(plan(), p, {"d2"})
    assert "Temple" not in view
    assert view.index("slow") < view.index("[d2]") < view.index("nothing early") < view.index("[i3]") < view.index("birthday")


def test_edits_touch_only_what_is_in_scope():
    before, after = plan(), plan()
    hotel = PlaceCandidate(place_id="mid", name="Niwa", category=PickCategory.STAY, tier="mid")
    cafe = PlaceCandidate(place_id="cafe", name="Cafe", category=PickCategory.EAT)
    DraftService._apply_edits(after, [
        PlanEdit(op="replace", item_id="i3", place="c1"),
        PlanEdit(op="replace", item_id="i1", place="c1"),  # out of scope
        PlanEdit(op="replace", item_id="s1", place="c0"),  # out of scope
        PlanEdit(op="remove", item_id="i2"),  # out of scope
    ], {"c0": hotel, "c1": cafe}, {"i3"}, set(), {"d1", "d2"})
    assert after.stay == before.stay and after.days[0] == before.days[0]
    museum, dinner = after.days[1].activities
    assert museum == before.days[1].activities[0]
    assert (dinner.id, dinner.name, dinner.time_of_day) == ("i3", "Cafe", "evening")


def test_the_stay_changes_alone_and_only_to_a_stay():
    before, after = plan(), plan()
    places = {"c0": PlaceCandidate(place_id="bar", name="Bar", category=PickCategory.DO),
              "c1": PlaceCandidate(place_id="mid", name="Niwa", category=PickCategory.STAY)}
    DraftService._apply_edits(after, [PlanEdit(op="replace", item_id="s1", place="c0")], places, {"s1"}, set(), set())
    assert after == before
    DraftService._apply_edits(after, [PlanEdit(op="replace", item_id="s1", place="c1")], places, {"s1"}, set(), set())
    assert after.stay.name == "Niwa" and after.days == before.days


def test_moving_an_item_keeps_its_id():
    after = plan()
    DraftService._apply_edits(after, [PlanEdit(op="move", item_id="i3", day_id="d1")], {}, {"i3"}, set(), {"d1", "d2"})
    assert [a.id for a in after.days[0].activities] == ["i1", "i3"]


def test_plans_and_notes_saved_before_ids_still_load():
    old = {"destination": "Rome", "days": [{"day_number": 1, "activities": [{"name": "Forum", "category": "do"}]}]}
    assert Itinerary.model_validate(old) == Itinerary.model_validate(old)
    note = Note.model_validate({"topic": "birthday", "text": "one big night", "scope": "moment", "toward": None})
    assert (note.target, note.once, note.text) == ("trip", True, "one big night")
    assert note.id == Note.model_validate({"topic": "birthday", "text": "one big night", "scope": "moment"}).id


def test_old_preferences_become_notes_on_the_next_turn(client, auth, db_session):
    from app.database.models.trip import TripModel
    trip = new_trip(client, auth)
    row = db_session.get(TripModel, trip["id"])
    row.profile = {"preferences": [{"key": "diet", "value": "vegan", "source": "stated"}]}
    db_session.commit()
    turn = say(client, auth, trip["id"], "hello")
    assert turn["trip"]["profile"]["preferences"] == []
    assert [(n["text"], n["category"], n["kind"]) for n in turn["trip"]["personality"]["notes"]] == [("vegan", "diet", "must")]


def test_drafts_say_which_stop_fulfils_each_moment_and_choosing_maps_the_note():
    places = [PlaceCandidate(place_id=f"p{i}", name=n, category=PickCategory.DO)
              for i, n in enumerate(["Rooftop Bar", "Old Museum"])]
    made = DraftPlan(title="t", summary="s", days=[PlanDay(theme="d", stops=[
        PlanStop(place="Rooftop Bar", ref=0, time_of_day="evening", note="party", for_moment=0),
        PlanStop(place="Old Museum", ref=1, time_of_day="morning", note="look", for_moment=7)])])
    itinerary = PlacePool(places).to_itinerary(made, "Rome", ["one big night out"])
    assert fulfilled(itinerary) == {"one big night out"}
    [note] = DraftService.map_moments(itinerary, update(add("one big night out", once=True)).notes)
    assert note.target == itinerary.days[0].activities[1].id


def test_picks_name_only_a_known_trait(monkeypatch):
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(lambda prompt, schema: Ranking(ranked=[
        RankedItem(ref=0, why="a", trait="food is the point."),
        RankedItem(ref=1, why="b", trait="Nightlife"),
        RankedItem(ref=2, why="c", trait="fun"),
    ])))
    places = [PlaceCandidate(place_id=f"p{i}", name=f"P{i}", category=PickCategory.EAT) for i in range(3)]
    picks = RankingService.rank(PickCategory.EAT, places, TripProfile(), personality=update(add("food", toward="food_focus")))
    assert [x.trait for x in picks] == ["Food is the point", None, None]


def test_cheaper_stay_is_one_note_and_one_change(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm, budget_level=BudgetLevel.BUDGET)
    draft = say(client, auth, trip["id"], "not sure")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth)
    fake_llm.decisions.append(TurnDecision(
        action=TurnAction.EDIT_ITINERARY, reply="On it.", profile_updates={"budget_level": "luxury"},
        note_changes=[add("similar but cheaper", "cheaper", target="s1", category="stay", then="change")]))
    fake_llm.edits = [{"op": "replace", "item_id": "s1", "place": "c0"}]

    turn = say(client, auth, trip["id"], "A cheaper hotel than this luxury one")

    after = turn["trip"]["itinerary"]
    assert after["stay"]["place_id"] != draft["itinerary"]["stay"]["place_id"]
    assert after["days"] == draft["itinerary"]["days"]
    assert turn["trip"]["profile"]["budget_level"] == "budget"  # one item never changes the trip's budget
    assert ("s1", "similar but cheaper") in [(n["target"], n["text"]) for n in turn["trip"]["personality"]["notes"]]


def test_removing_a_stop_moves_its_notes_to_the_day(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "not sure")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth)
    item = draft["itinerary"]["days"][0]["activities"][0]["id"]
    fake_llm.decisions.append(TurnDecision(action=TurnAction.CHAT, reply="ok", note_changes=[add("birthday", target=item)]))
    say(client, auth, trip["id"], "that's for the birthday")

    result = client.post(f"/trips/{trip['id']}/itinerary/days/1/activities/0", json={"action": "remove"}, headers=auth).json()

    assert ("d1", "birthday") in [(n["target"], n["text"]) for n in result["trip"]["personality"]["notes"]]
