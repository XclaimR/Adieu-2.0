import json

import pytest

from app.schemas.trip_profile import BudgetLevel, Party, ProfileUpdates
from app.schemas.personality import NoteChange
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction, TurnDecision
from app.services.llm_client import LLMClient


def new_trip(client, auth):
    return client.post("/trips", json={}, headers=auth).json()


def say(client, auth, trip_id, message):
    return client.post(f"/trips/{trip_id}/messages", json={"message": message}, headers=auth).json()


def decision(action=TurnAction.ASK, notes=(), **profile):
    return TurnDecision(profile_updates=ProfileUpdates(**profile), note_changes=list(notes), action=action,
                        reply="Lovely.")


def mood(word, quote):
    return NoteChange(op="add", text=word, quote=quote, mood=word)


def ready_trip(client, auth, fake_llm, **extra):
    """A trip with the minimum vibe known and duration already asked, so the next turn drafts plans."""
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon", notes=[mood("relaxed", "relaxed")], party=Party.COUPLE, **extra))
    say(client, auth, trip["id"], "Lisbon, relaxed, with my partner, on a budget")  # asks duration
    return trip


def test_routes_require_sign_in(client):
    assert client.get("/trips").status_code == 401
    assert client.get("/trips", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_vibe_picker_saves_valid_vibes_only(client, auth):
    user = client.post("/me/taste", json={"vibes": ["food", "beach", "not-a-vibe"]}, headers=auth).json()
    assert user["taste"]["vibes"] == ["food", "beach"]


def test_new_trip_asks_for_destination_with_chips(client, auth):
    trip = new_trip(client, auth)
    assert trip["profile"]["destination"] is None
    assert trip["next_question"] == "Where would you like to go?"
    assert "Surprise me" in trip["answer_options"]
    assert trip["panel"] is None and trip["drafts"] == []


def test_destination_from_a_sentence_is_resolved_and_mood_is_asked_next(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Japan"))

    turn = say(client, auth, trip["id"], "I want to go to Japan")

    assert turn["trip"]["profile"]["destination"] == "Japan"
    assert turn["trip"]["destination_name"] == "Japan"
    assert turn["next_question"] == "What kind of trip are you dreaming of?"
    assert "Relaxed" in turn["answer_options"]
    assert turn["options"] is None and turn["drafts"] == []


def test_unknown_destination_is_not_kept(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Atlantis"))

    turn = say(client, auth, trip["id"], "Atlantis")

    assert turn["trip"]["profile"]["destination"] is None
    assert "couldn't find" in turn["reply"]


def test_no_options_until_the_vibe_is_known(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon"))
    say(client, auth, trip["id"], "Lisbon")
    # The model wants to show options, but the user didn't ask and the mood is unknown.
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Here!",
                                           options_request=OptionsRequest(kind=OptionsKind.STAY)))

    turn = say(client, auth, trip["id"], "we love good food")

    assert turn["options"] is None
    assert turn["next_question"] == "What kind of trip are you dreaming of?"


def test_explicit_requests_are_honoured_before_the_vibe_is_complete(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon"))
    say(client, auth, trip["id"], "Lisbon")
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.EAT, query="cafés near Alfama")))

    turn = say(client, auth, trip["id"], "show me cafés near Alfama")

    assert turn["options"]["kind"] == "eat"
    assert [p["place_id"] for p in turn["options"]["items"]] == ["eat-1", "eat-0"]
    assert turn["trip"]["panel"]["id"] == turn["options"]["id"]


def test_a_destination_is_loaded_once_then_turns_ask_nobody_outside(client, auth, fake_llm, fake_places):
    trip = ready_trip(client, auth, fake_llm)
    say(client, auth, trip["id"], "not sure about days")  # duration was asked once -> drafts
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.EAT, query="")))
    say(client, auth, trip["id"], "show me where to eat")

    assert [a["place_id"] for a in fake_places.loads] == ["Q-Lisbon"]
    assert fake_places.resolved == ["Lisbon"]
    saved = client.get(f"/trips/{trip['id']}", headers=auth).json()
    assert saved["destination_name"] == "Lisbon"


def test_complete_vibe_offers_three_drafts_using_only_real_places(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    assert trip  # duration question was asked in ready_trip

    turn = say(client, auth, trip["id"], "not sure about days")

    assert len(turn["drafts"]) == 3
    assert turn["trip"]["profile"]["duration_days"] == 4  # default when skipped
    draft = turn["drafts"][0]["itinerary"]
    place_ids = [a["place_id"] for d in draft["days"] for a in d["activities"]]
    assert place_ids == ["do-0", "eat-0", "do-1", "gem-0"]  # the invented ref 999 was dropped
    assert draft["stay"]["place_id"] == "stay-0"


def test_stuck_user_gets_drafts_even_with_only_a_destination(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon"))
    say(client, auth, trip["id"], "Lisbon")
    stuck = decision()
    stuck.user_stuck = True
    fake_llm.decisions.append(stuck)

    turn = say(client, auth, trip["id"], "hmm no idea, you decide")

    assert len(turn["drafts"]) == 3


def test_show_plans_button_offers_drafts_and_reads_naturally(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon"))
    say(client, auth, trip["id"], "Lisbon")

    turn = say(client, auth, trip["id"], "__show_plans__")

    assert len(turn["drafts"]) == 3
    assert "Show me some plans" in [m["content"] for m in turn["trip"]["messages"]]


def test_drafts_offered_after_four_questions_without_progress(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon"))
    say(client, auth, trip["id"], "Lisbon")
    for _ in range(4):
        turn = say(client, auth, trip["id"], "hmm")
        assert turn["drafts"] == []

    turn = say(client, auth, trip["id"], "hmm")
    assert len(turn["drafts"]) == 3


def test_surprise_me_shows_only_verified_destination_ideas(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Thailand"))

    turn = say(client, auth, trip["id"], "surprise me")

    assert turn["trip"]["profile"]["destination"] is None
    assert turn["options"]["kind"] == "destinations"
    assert [i["destination"] for i in turn["options"]["ideas"]] == ["Japan"]


def test_choosing_a_draft_sets_the_itinerary_and_asks_for_changes(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "not sure")["drafts"][1]

    chosen = client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth).json()

    assert chosen["itinerary"]["title"] == draft["title"]
    assert "Anything you'd like to change?" in chosen["next_question"]
    assert chosen["answer_options"]


def test_liking_an_over_budget_stay_suggests_similar_cheaper_ones(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm, budget_level=BudgetLevel.BUDGET)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.STAY, query="")))
    # Budget traveler: the luxury resort is filtered out of what we show...
    shown = say(client, auth, trip["id"], "show me places to stay")["options"]["items"]
    assert "stay-2" not in [p["place_id"] for p in shown]

    # ...but if they like a mid-tier hotel anyway, we offer similar budget ones.
    result = client.post(f"/trips/{trip['id']}/picks/stay-1", json={"action": "like"}, headers=auth).json()

    assert result["trip"]["liked_place_ids"] == ["stay-1"]
    assert result["options"]["title"] == "Like City Hotel, likely for less (estimated)"
    assert all(p["tier"] == "budget" for p in result["options"]["items"])


def test_liking_a_place_is_a_signal_for_this_trip_only(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.EAT, query="")))
    say(client, auth, trip["id"], "show me where to eat")

    result = client.post(f"/trips/{trip['id']}/picks/eat-1", json={"action": "like"}, headers=auth).json()

    assert result["options"] is None
    assert result["trip"]["itinerary"] is None  # liking is not adding
    assert [(n["text"], n["source"]) for n in result["trip"]["personality"]["notes"]][-1] == \
        ("likes Seafood Restaurant", "inferred")
    assert client.get("/me", headers=auth).json()["taste"]["likes"] == []


def test_add_puts_places_in_the_itinerary(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.STAY, query="")))
    say(client, auth, trip["id"], "show me places to stay")

    result = client.post(f"/trips/{trip['id']}/picks/stay-1", json={"action": "add"}, headers=auth).json()

    assert result["trip"]["itinerary"]["stay"]["place_id"] == "stay-1"


def test_add_refuses_a_place_outside_the_route(client, auth, fake_llm, db_session):
    from app.database.models import TripModel
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.STAY, query="")))
    say(client, auth, trip["id"], "show me places to stay")
    row = db_session.get(TripModel, trip["id"])
    row.itinerary = {"destination": "Japan", "days": [{"day_number": 1, "city": "Faraway", "activities": []}],
                     "route": [{"name": "Faraway", "lat": 40.0, "lng": 40.0}]}  # the places are at 1.5, 1.5
    db_session.commit()

    result = client.post(f"/trips/{trip['id']}/picks/stay-1", json={"action": "add"}, headers=auth)

    assert result.status_code == 404 and "outside your route (Faraway)" in result.json()["detail"]


def test_swap_and_remove_activities(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "not sure")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth)
    base = f"/trips/{trip['id']}/itinerary/days/1/activities"

    swap = client.post(f"{base}/0", json={"action": "swap"}, headers=auth).json()
    assert swap["options"]["kind"] == "swap"
    alternative = swap["options"]["items"][0]["place_id"]
    assert alternative not in ("do-0", "do-1")  # already in the plan

    replaced = client.post(f"{base}/0/replace", json={"place_id": alternative}, headers=auth).json()
    assert replaced["itinerary"]["days"][0]["activities"][0]["place_id"] == alternative
    assert replaced["itinerary"]["days"][0]["activities"][0]["time_of_day"] == "morning"

    removed = client.post(f"{base}/1", json={"action": "remove"}, headers=auth).json()
    assert len(removed["trip"]["itinerary"]["days"][0]["activities"]) == 1


def test_chat_edits_the_chosen_itinerary(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "not sure")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth)
    edit = TurnDecision(action=TurnAction.EDIT_ITINERARY, reply="Done.", edit_request="make day 2 more relaxed",
                        note_changes=[NoteChange(op="add", target="d2", text="more relaxed", quote="more relaxed",
                                                 then="change")])
    second = draft["itinerary"]["days"][1]["activities"]
    fake_llm.edits = [{"op": "remove", "item_id": second[1]["id"]}]
    fake_llm.decisions.append(edit)

    turn = say(client, auth, trip["id"], "make day 2 more relaxed")

    edit_prompts = [p for name, p in fake_llm.prompts if name == "PlanEdits"]
    assert "make day 2 more relaxed" in edit_prompts[-1]
    days = turn["trip"]["itinerary"]["days"]
    assert days[0] == draft["itinerary"]["days"][0]  # untouched: same stops, notes and ids
    assert days[1]["activities"] == second[:1]
    assert turn["next_question"] == "Anything you'd like to change?"


def test_stream_sends_progress_reply_drafts_then_done(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)

    with client.stream("POST", f"/trips/{trip['id']}/messages/stream", json={"message": "not sure"}, headers=auth) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]

    types = [e["type"] for e in events]
    assert types[0] == "progress"
    assert types.index("reply") < types.index("draft")
    assert types.count("draft") == 3
    assert types[-1] == "done"
    assert len(events[-1]["trip"]["drafts"]) == 3


def test_users_cannot_see_each_others_trips(client, auth):
    trip = new_trip(client, auth)
    other = client.post("/auth/dev", json={"email": "b@example.com"}).json()["token"]
    resp = client.get(f"/trips/{trip['id']}", headers={"Authorization": f"Bearer {other}"})
    assert resp.status_code == 404


def test_reset_clears_the_trip(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    say(client, auth, trip["id"], "start over")
    turn = say(client, auth, trip["id"], "yes")
    assert turn["trip"]["profile"]["destination"] is None
    assert turn["trip"]["drafts"] == []


def test_destination_never_resolves_to_a_business(client, auth, fake_llm, fake_places):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Korea"))

    turn = say(client, auth, trip["id"], "I want to bachelor party in Korea")

    assert turn["trip"]["destination_name"] == "South Korea"


def test_choosing_a_draft_mid_stream_keeps_the_conversation_in_order(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)

    with client.stream("POST", f"/trips/{trip['id']}/messages/stream", json={"message": "a week"}, headers=auth) as r:
        for line in r.iter_lines():
            event = json.loads(line) if line else {}
            if event.get("type") == "draft" and event["index"] == 0:
                client.post(f"/trips/{trip['id']}/drafts/{event['draft']['id']}/choose", headers=auth)
            if event.get("type") == "done":
                done = event["trip"]

    contents = [m["content"] for m in done["messages"]]
    assert contents.index("a week") < next(i for i, c in enumerate(contents) if c.startswith("Great choice"))
    assert done["itinerary"] is not None
    assert done["next_question"].startswith("Great choice")


def test_starter_ideas_are_cached_per_user(client, auth, fake_llm):
    first = client.get("/starter-ideas", headers=auth).json()
    second = client.get("/starter-ideas", headers=auth).json()

    assert [i["destination"] for i in first] == ["Japan"]
    assert second == first
    assert [name for name, _ in fake_llm.prompts].count("StarterIdeas") == 1


def test_trip_list_shows_the_chosen_plan_name_and_length(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "4 days")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth)

    [summary] = client.get("/trips", headers=auth).json()

    assert summary["title"] == draft["title"]
    assert summary["duration_days"] == 4


def test_friends_trip_tells_the_planner_to_skip_romantic_spots(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon", notes=[mood("foodie", "food trip")], party=Party.FRIENDS))
    say(client, auth, trip["id"], "Lisbon food trip with friends")  # asks duration
    say(client, auth, trip["id"], "not sure")  # drafts

    draft_prompts = [p for name, p in fake_llm.prompts if name == "DraftPlan"]
    assert draft_prompts and all("group of friends" in p and "avoid romantic" in p for p in draft_prompts)


def planned_trip(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "4 days")["drafts"][0]
    return client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth).json()


@pytest.mark.parametrize("message", [
    "I need a mental reset",
    "Let's do “Wellness reset” in Japan",
    "can we start over breakfast on day 2?",
    "somewhere to reset and recharge, please",
    "don't reset",
    "no need to start over",
    "presets",
    "Reset and recharge in Bali",
])
def test_reset_words_inside_a_message_keep_the_trip(client, auth, fake_llm, message):
    before = planned_trip(client, auth, fake_llm)

    turn = say(client, auth, before["id"], message)

    assert turn["reply"] != "Fresh start!"
    assert "Are you sure" not in (turn["next_question"] or "")  # not even asked
    assert turn["trip"]["itinerary"] == before["itinerary"]
    assert turn["trip"]["drafts"] == before["drafts"]
    assert turn["trip"]["profile"]["destination"] == before["profile"]["destination"]


def test_tapping_an_idea_called_reset_sets_the_destination(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Japan"))

    turn = say(client, auth, trip["id"], "Let's do “Wellness reset” in Japan")

    assert turn["trip"]["profile"]["destination"] == "Japan"


def assert_kept(turn, before):
    assert turn["reply"] != "Fresh start!"
    assert turn["trip"]["itinerary"] == before["itinerary"]
    assert turn["trip"]["drafts"] == before["drafts"]
    assert turn["trip"]["profile"] == before["profile"]


@pytest.mark.parametrize("message", [
    "reset", "Reset.", "Start over!", "start over?", "please start over", "START OVER, please", "“reset”",
    "Please, reset 🙏",
])
def test_a_reset_command_asks_before_deleting_anything(client, auth, fake_llm, message):
    before = planned_trip(client, auth, fake_llm)
    calls = len(fake_llm.prompts)

    turn = say(client, auth, before["id"], message)

    assert_kept(turn, before)
    assert "Are you sure you want to start from scratch?" in turn["next_question"]
    assert turn["answer_options"] == ["Yes, start from scratch", "No, keep my trip"]
    assert turn["trip"]["answer_options"] == turn["answer_options"]  # still there after a reload
    assert len(fake_llm.prompts) == calls


@pytest.mark.parametrize("answer", [
    "Yes, start from scratch", "yes", "Yes!", "yeah", "yep", "sure", "yes please", "Yes, I’m sure", "I'm sure",
    "confirm", "yes, reset",
])
def test_a_clear_yes_resets(client, auth, fake_llm, answer):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")

    turn = say(client, auth, before["id"], answer)

    assert turn["reply"] == "Fresh start!"
    assert turn["next_question"] == "Where would you like to go?"
    assert turn["trip"]["itinerary"] is None and turn["trip"]["drafts"] == []
    assert turn["trip"]["profile"]["destination"] is None


@pytest.mark.parametrize("answer", ["No, keep my trip", "no", "Nope.", "no thanks", "cancel", "never mind", "don’t"])
def test_a_no_keeps_the_trip_and_returns_to_the_question_before(client, auth, fake_llm, answer):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")
    calls = len(fake_llm.prompts)

    turn = say(client, auth, before["id"], answer)

    assert_kept(turn, before)
    assert (turn["next_question"], turn["answer_options"]) == (before["next_question"], before["answer_options"])
    assert len(fake_llm.prompts) == calls


@pytest.mark.parametrize("answer", ["yes?", "not sure", "yes but keep the hotel", "maybe", "ok wait", "__show_plans__"])
def test_anything_but_a_clear_yes_does_not_reset(client, auth, fake_llm, answer):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")

    turn = say(client, auth, before["id"], answer)

    assert turn["reply"].startswith("I didn't start over.")
    assert turn["trip"]["itinerary"] == before["itinerary"]
    assert turn["trip"]["profile"] == before["profile"]


def test_the_model_is_told_the_trip_was_not_reset(client, auth, fake_llm, monkeypatch):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")
    seen = []

    def model(prompt, schema, history=None, **kwargs):
        seen.extend(history or [])
        return fake_llm(prompt, schema)

    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(model))
    say(client, auth, before["id"], "maybe")

    assert seen[-1] == {"role": "assistant", "content": "I didn't start over."}


def test_a_yes_after_moving_on_does_not_reset(client, auth, fake_llm):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")
    say(client, auth, before["id"], "what's the weather like?")

    assert_kept(say(client, auth, before["id"], "yes"), before)


def test_a_yes_after_choosing_a_plan_does_not_reset(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "4 days")["drafts"][0]
    say(client, auth, trip["id"], "reset")
    chosen = client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth).json()

    assert_kept(say(client, auth, trip["id"], "yes"), chosen)


def test_a_yes_with_no_reset_question_does_not_reset(client, auth, fake_llm):
    before = planned_trip(client, auth, fake_llm)

    assert_kept(say(client, auth, before["id"], "Yes, start from scratch"), before)


def test_asking_twice_is_not_a_yes(client, auth, fake_llm):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")

    again = say(client, auth, before["id"], "reset")

    assert_kept(again, before)
    assert "Are you sure" in again["next_question"]
    assert say(client, auth, before["id"], "no")["next_question"] == before["next_question"]


def test_confirming_works_over_the_stream(client, auth, fake_llm):
    before = planned_trip(client, auth, fake_llm)

    def stream(message):
        with client.stream("POST", f"/trips/{before['id']}/messages/stream", json={"message": message},
                           headers=auth) as response:
            return {e["type"]: e for e in map(json.loads, response.iter_lines())}

    asked = stream("reset")
    assert "Are you sure" in asked["reply"]["next_question"]
    assert asked["done"]["trip"]["itinerary"] == before["itinerary"]
    assert stream("Yes, start from scratch")["done"]["trip"]["itinerary"] is None


def test_the_reply_shown_while_drafting_also_says_nothing_was_reset(client, auth, fake_llm):
    before = planned_trip(client, auth, fake_llm)
    say(client, auth, before["id"], "reset")

    with client.stream("POST", f"/trips/{before['id']}/messages/stream", json={"message": "__show_plans__"},
                       headers=auth) as response:
        replies = [e["text"] for e in map(json.loads, response.iter_lines()) if e["type"] == "reply"]

    assert len(replies) == 2  # while drafting, then once the plans exist
    assert all(text.startswith("I didn't start over.") for text in replies)
