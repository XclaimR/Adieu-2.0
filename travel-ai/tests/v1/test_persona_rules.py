"""The persona suite's own rule checks, on canned trips (the suite itself runs against the live app)."""
from tests.personas.personas import PERSONAS
from tests.personas.run import check, converse, invented_facts, meal_only_slots, misfit_picks, pick_reply

BACHELOR = next(p for p in PERSONAS if p["name"] == "bachelor")
BACKPACKER = next(p for p in PERSONAS if p["name"] == "budget_backpacker")
FAMILY = next(p for p in PERSONAS if p["name"] == "family_beach")


def test_group_of_friends_with_a_size_is_invented():
    profile = {"party": "friends", "party_size": 6, "mood": ["nightlife"]}
    assert invented_facts(profile, BACHELOR["facts"]) == ["party_size=6"]


def test_unset_fields_and_stated_moods_pass():
    assert invented_facts({"party": "friends", "mood": ["Fun", "party"]}, BACHELOR["facts"]) == []
    assert invented_facts({"mood": ["romantic"]}, BACHELOR["facts"]) == ["mood='romantic'"]


def test_misfit_picks_flags_words_and_pricey_stays():
    stay = {"name": "Grand Hotel", "tier": "luxury", "price_level": "$$$$"}
    trip = {"panel": None, "drafts": [{"title": "Saigon", "summary": "A luxury escape", "itinerary": {
        "stay": stay, "days": [{"activities": [{"name": "Street food walk", "description": "Barcelona-style"}]}]}}]}
    assert misfit_picks(trip, BACKPACKER) == [
        "Grand Hotel: $$$$", "Grand Hotel: tier luxury", "draft 'Saigon': 'luxury'"]


def test_pick_reply_uses_script_once_then_chips_then_gives_up():
    used = set()
    assert pick_reply(BACKPACKER, "Who's coming along?", ["Just me", "Friends"], used)[0] == "Just me."
    assert pick_reply(BACKPACKER, "Who's coming along?", ["Just me", "Friends"], used) == ("Just me", {})
    assert pick_reply(BACKPACKER, "Any diet needs?", ["Vegan"], used)[0] == "just show me plans"


class FakeApi:
    """Answers the persona's turns from a script instead of the live app."""
    def __init__(self, turns):
        self.turns, self.token = list(turns), None

    def post(self, path, body):
        if path == "/auth/dev":
            return {"token": "t"}
        trip = {"id": "trip1", "profile": {}, "drafts": [], "panel": None}
        if path == "/trips":
            return trip
        question, drafts = self.turns.pop(0)
        trip["drafts"] = [{"title": "Beach days", "summary": "", "itinerary": {"days": []}}] * drafts
        return {"next_question": question, "answer_options": [], "drafts": trip["drafts"], "options": None,
                "trip": trip}


def drafts_rule(api):
    return next(r for r in check(FAMILY, converse(api, FAMILY)) if r[0] == "drafts once vibe is known")


def test_drafts_may_wait_for_the_duration_and_trip_ends_questions():
    # Destination arrives on turn 2; duration wasn't said yet, so the app may ask it once, then where the trip
    # starts and where it ends (Ticket #81), before drafting.
    api = FakeApi([("Where to?", 0), ("How many days?", 0), ("Which city are you starting from?", 0),
                   ("Flying back to London at the end, or a different city?", 0), (None, 3)])
    assert drafts_rule(api) == ("drafts once vibe is known", True, "due by turn 5, drafts at turn 5")


def test_drafts_late_after_those_questions_fail():
    api = FakeApi([("Where to?", 0), ("How many days?", 0), ("Which city are you starting from?", 0),
                   ("Flying back to London at the end, or a different city?", 0), ("Any diet needs?", 0), (None, 3)])
    assert drafts_rule(api)[1] is False


def test_meal_only_slots_are_flagged_but_an_evening_bar_is_an_outing():
    def stop(name, category, when, description=None):
        return {"name": name, "category": category, "time_of_day": when, "description": description}
    day = {"day_number": 1, "activities": [
        stop("Museum", "do", "morning"), stop("Trattoria", "eat", "afternoon"),
        stop("Osteria", "eat", "evening"), stop("Rooftop", "eat", "evening", "Cocktails at sunset")]}
    trip = {"drafts": [{"title": "Rome", "itinerary": {"days": [day]}}]}
    assert meal_only_slots(trip) == ["'Rome' day 1 afternoon: Trattoria"]


def test_route_gaps_need_a_stay_per_city_legs_and_nights_that_add_up():
    from tests.personas.run import route_gaps
    city = lambda name, stay, leg, nights: {"name": name, "stay": stay and {"place_id": stay}, "leg": leg,
                                            "nights": nights}
    good = {"title": "ok", "itinerary": {"days": [{}] * 4, "route": [
        city("Tokyo", "a", None, 2), city("Kyoto", "b", {"mode": "train", "minutes": 135}, 1)]}}
    same_hotel = {"title": "same", "itinerary": {"days": [{}] * 4, "route": [
        city("Tokyo", "a", None, 2), city("Kyoto", "a", None, 3)]}}
    assert route_gaps({"drafts": [good]}) == []
    gaps = route_gaps({"drafts": [same_hotel, {"title": "none", "itinerary": {"days": []}}]})
    assert any("separate stay" in g for g in gaps) and any("leg" in g for g in gaps)
    assert any("add up" in g for g in gaps) and any("no route" in g for g in gaps)
