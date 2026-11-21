from app.schemas.personality import NoteUpdate, Personality, Scale
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.taste import Taste
from app.schemas.trip_profile import BudgetLevel, Party, TripProfile
from app.schemas.turn import TurnAction, TurnDecision
from app.services.llm_client import LLMClient
from app.services.personality_service import PersonalityService
from app.services.places_service import traveler_fit
from app.services.ranking_service import Ranking, RankedItem, RankingService
from tests.v1.test_api import new_trip, say


def notes(*pairs):
    """(toward, quote) pairs as trip notes the model proposed."""
    return [NoteUpdate(quote=q, topic=t, scope="trip", toward=t) for t, q in pairs]


def card(message="", signals=(), profile=TripProfile(), taste=Taste(), stored=None):
    return PersonalityService.card(PersonalityService.update(stored, profile, taste, notes(*signals), message))


def test_lasting_taste_seeds_leanings_but_the_card_shows_them_as_unknown():
    p = card(taste=Taste(vibes=["food", "nightlife", "wellness"]))
    assert p.scales["food"].confidence < 0.5 and p.scales["food"].value > 0
    assert p.traits == []
    assert p.unknown == ["Pace", "Money", "Energy", "Crowds", "Food", "Comfort"]
    known, leans = PersonalityService.traits(p)
    assert known == [] and "Food is the point" in leans


def test_the_travelers_own_words_set_the_matching_scales():
    p = card("A slow food week, away from the crowds", profile=TripProfile(budget_level=BudgetLevel.LUXURY))
    assert p.traits == ["Slow pace", "Avoids crowds", "Food is the point", "Luxury"]
    assert p.unknown == ["Money", "Energy"]


def test_model_written_mood_words_dont_count_only_what_they_said():
    # The model stored mood "luxury" for a spa wish; the card reads the message, not the mood.
    assert card("Paris with my partner", profile=TripProfile(mood=["luxury", "relaxed"])).traits == []


def test_vague_words_and_single_activities_set_nothing():
    for message in ("Planning a fun trip to Spain!", "We're adventurous", "Paris, and one spa afternoon",
                    "A slow week, but one big night out for my birthday"):
        traits = card(message, signals=[("luxury", "spa afternoon"), ("nightlife", "one big night out")]).traits
        assert not any(t.startswith(("Luxury", "Nightlife", "Packed")) for t in traits), message
    assert card("A slow week, but one big night out").traits == ["Slow pace"]
    assert card("One week in Lisbon, slow").traits == ["Slow pace"]  # a trip length isn't a one-off


def test_negation_flips_only_where_no_x_means_the_other_pole():
    assert card("No nightlife, lots of food").traits == ["Chill evenings", "Food is the point (strongly)"]
    for message in ("Not cheap", "No luxury please", "Not too packed"):
        assert card(message, signals=[("packed", "not too packed"), ("slow", "not too packed")]).traits == [], message


def test_intensity_and_certainty_come_from_their_words():
    assert card("We want it really slow").traits == ["Slow pace (strongly)"]
    assert card("A bit slow is fine").traits == ["Slow pace (a little)"]
    assert card("Maybe slow? Not sure").traits == []  # a guess stays unknown
    # The model read "quiet" as chill evenings: no listed word confirms it, so it stays a weak guess.
    p = card("we want it really slow and quiet", signals=[("chill", "quiet")])
    assert p.traits == ["Slow pace (strongly)"] and "Chill evenings" in PersonalityService.traits(p)[1]


def test_model_only_poles_are_weak_guesses_and_need_their_words():
    msg = "Barcelona, avoid the tourist traps, we want what locals love"
    assert card(msg).traits == ["Avoids crowds"]  # the list reads "avoid the tourist traps" on its own
    # "exploring" read as hidden gems (seen live on Gemini): kept as a guess, never shown as known.
    p = card("Adventurous: street food and exploring", signals=[("hidden_gems", "exploring")])
    assert p.traits == ["Food is the point"] and "Avoids crowds" in PersonalityService.traits(p)[1]
    assert card(msg, signals=[("slow", "avoid tourist traps")]).traits == ["Avoids crowds"]  # "slow": not their words
    assert card("honestly just doing nothing", signals=[("luxury", "prioritizing comfort")]).traits == []  # invented
    assert card("Something fun!", signals=[("nightlife", "fun")]).traits == []  # vague
    assert card("partying", signals=[("icons", "art")]).traits == ["Nightlife"]  # "art" isn't a word in "partying"
    fun = "Planning a fun trip to Japan with friends!"
    assert card(fun, signals=[("slow", fun)]).traits == []  # a whole sentence isn't proof
    assert card(fun, signals=[("slow", "fun trip to Japan")], profile=TripProfile(destination="Japan")).traits == []
    assert card("A slow week", signals=[("nightlife", "slow week")]).traits == ["Slow pace"]  # about another scale


def test_family_and_budget_are_checked_facts():
    assert card(profile=TripProfile(party=Party.FAMILY)).traits == ["Chill evenings"]
    # A weak model-only guess ("beach holiday" read as chill) doesn't wipe the fact (seen live).
    assert card("a beach holiday", signals=[("chill", "beach holiday")],
                profile=TripProfile(party=Party.FAMILY)).traits == ["Chill evenings"]
    assert card(profile=TripProfile(budget_level=BudgetLevel.MID)).traits == ["Mid-range comfort"]


def test_answers_from_elsewhere_are_kept():
    asked = Personality(scales={"money": Scale(value=0.9, confidence=0.9, source="asked")})
    p = card(profile=TripProfile(budget_level=BudgetLevel.BUDGET), taste=Taste(vibes=["luxury"]), stored=asked)
    assert p.traits == ["Money goes to experiences (strongly)", "Backpacker comfort"]


def test_prompts_get_known_traits_and_weak_guesses_separately():
    p = PersonalityService.update(None, TripProfile(party=Party.SOLO), Taste(vibes=["nightlife"]), message="slow")
    text = traveler_fit(TripProfile(party=Party.SOLO), p)
    assert "Solo traveler" in text
    assert "personality: Slow pace." in text
    assert "Weak guesses, from their usual taste or a hint (don't over-weight): Nightlife." in text
    assert traveler_fit(TripProfile(), None) == ""


def test_picks_name_only_a_known_trait(monkeypatch):
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(lambda prompt, schema: Ranking(ranked=[
        RankedItem(ref=0, why="a", trait="food is the point."),
        RankedItem(ref=1, why="b", trait="Nightlife"),  # only a weak guess: not shown
        RankedItem(ref=2, why="c", trait="fun"),
    ])))
    p = PersonalityService.update(None, TripProfile(), Taste(vibes=["nightlife"]), message="foodie trip")
    places = [PlaceCandidate(place_id=f"p{i}", name=f"P{i}", category=PickCategory.EAT) for i in range(3)]
    picks = RankingService.rank(PickCategory.EAT, places, TripProfile(), Taste(), personality=p)
    assert [x.trait for x in picks] == ["Food is the point", None, None]


def test_what_they_said_persists_across_turns_drives_drafts_and_newest_wins(client, auth, fake_llm):
    client.post("/me/taste", json={"vibes": ["food"]}, headers=auth)
    trip = new_trip(client, auth)
    assert trip["personality"]["traits"] == [] and len(trip["personality"]["unknown"]) == 6
    assert trip["personality"]["scales"]["food"]["source"] == "taste"

    turn = say(client, auth, trip["id"], "Lisbon, and we want it really slow")
    assert turn["trip"]["personality"]["traits"] == ["Slow pace (strongly)"]

    turn = say(client, auth, trip["id"], "what about Porto?")  # says nothing about pace
    assert turn["trip"]["personality"]["traits"] == ["Slow pace (strongly)"]

    turn = say(client, auth, trip["id"], "Hmm, actually packed days")
    assert turn["trip"]["personality"]["traits"] == ["Packed days"]


def note(quote, topic, scope="trip", toward=None):
    return NoteUpdate(quote=quote, topic=topic, scope=scope, toward=toward)


def test_notes_keep_their_words_and_code_decides_what_is_a_moment():
    msg = "A slow week in Rome, but one big night out for my birthday!"
    p = PersonalityService.update(None, TripProfile(destination="Rome"), Taste(), [
        note("a slow week", "pace", toward="slow"),
        note("one big night out for my birthday", "birthday", scope="trip", toward="nightlife"),  # model said trip
        note("party every night", "nights"),  # not their words
        note("Rome", "destination"),  # nothing but the place
    ], msg)
    assert [(n.topic, n.text, n.scope, n.toward) for n in p.notes] == [
        ("pace", "a slow week", "trip", "slow"),
        ("birthday", "one big night out for my birthday", "moment", None),
    ]
    assert PersonalityService.card(p).traits == ["Slow pace"]
    assert PersonalityService.moments(p) == ["one big night out for my birthday"]


def test_a_note_on_the_same_topic_replaces_the_old_one_and_dropped_topics_go():
    p = PersonalityService.update(None, TripProfile(), Taste(), [note("a slow week", "pace"),
                                                                note("one cooking class", "cooking", "moment")],
                                  "A slow week, and one cooking class")
    p = PersonalityService.update(p, TripProfile(), Taste(), [note("packed days", "pace")],
                                  "Actually packed days, and skip the cooking class", drop_topics=["cooking"])
    assert [(n.topic, n.text) for n in p.notes] == [("pace", "packed days")]
    assert PersonalityService.card(p).traits == ["Packed days"]


def test_prompts_get_the_notes_and_numbered_moments():
    p = PersonalityService.update(None, TripProfile(), Taste(), [
        note("what locals love", "crowds"), note("one big night out", "birthday", "moment")],
        "We want what locals love, and one big night out")
    text = traveler_fit(TripProfile(), p)
    assert "whole trip, in their words: 'what locals love'." in text
    assert "Moments they want to fit in: [m0] 'one big night out'." in text


def test_drafts_say_which_stop_fulfils_each_moment_and_what_is_left():
    from app.services.draft_service import DraftPlan, PlacePool, PlanDay, PlanStop, fulfilled
    places = [PlaceCandidate(place_id=f"p{i}", name=n, category=PickCategory.DO)
              for i, n in enumerate(["Rooftop Bar", "Old Museum"])]
    plan = DraftPlan(title="t", summary="s", days=[PlanDay(theme="d", stops=[
        PlanStop(place="Rooftop Bar", ref=0, time_of_day="evening", note="party", for_moment=0),
        PlanStop(place="Old Museum", ref=1, time_of_day="morning", note="look", for_moment=7)])])
    itinerary = PlacePool(places).to_itinerary(plan, "Rome", ["one big night out", "a cooking class"])
    assert [a.for_note for a in itinerary.days[0].activities] == [None, "one big night out"]  # 7 doesn't exist
    assert fulfilled(itinerary) == {"one big night out"}


def test_a_quote_may_run_across_a_comma():
    msg = "Backpacking Vietnam on a shoestring, every night out partying!"
    p = PersonalityService.update(None, TripProfile(), Taste(), [
        note("Backpacking Vietnam on a shoestring, every night out partying", "style", toward="nightlife")], msg)
    assert [(n.scope, n.toward) for n in p.notes] == [("trip", "nightlife")]
    # Spanning a one-off phrase makes the whole note a moment.
    p = PersonalityService.update(None, TripProfile(), Taste(), [
        note("a slow week, but one big night out", "mix")], "A slow week, but one big night out")
    assert p.notes[0].scope == "moment"


def test_one_note_per_topic_per_message_and_dropping_a_note_drops_its_trait():
    p = PersonalityService.update(None, TripProfile(), Taste(), [
        note("a slow week", "pace", toward="slow"), note("slow mornings", "pace", toward="slow")],
        "A slow week, slow mornings")
    assert [n.text for n in p.notes] == ["slow mornings"]
    assert PersonalityService.card(p).traits == ["Slow pace"]
    p = PersonalityService.update(p, TripProfile(), Taste(), [], "forget that, we're flexible", drop_topics=["pace"])
    assert p.notes == [] and PersonalityService.card(p).traits == []


def test_the_model_sees_the_notes_so_it_can_replace_or_drop_them(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.ASK, reply="ok", notes=[
        note("one cooking class", "cooking", "moment")]))
    say(client, auth, trip["id"], "Lisbon, and one cooking class")
    fake_llm.decisions.append(TurnDecision(action=TurnAction.ASK, reply="ok", drop_note_topics=["cooking"]))
    turn = say(client, auth, trip["id"], "Skip the cooking class")
    turn_prompts = [p for name, p in fake_llm.prompts if name == "TurnDecision"]
    assert "What they told us so far (topic: their words): cooking: 'one cooking class'" in turn_prompts[-1]
    assert turn["trip"]["personality"]["notes"] == []
