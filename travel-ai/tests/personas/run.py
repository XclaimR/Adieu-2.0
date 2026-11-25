"""Runs the test travelers against the running app over HTTP and prints pass/fail per persona per rule.

    docker compose exec api python -m tests.personas.run
    python -m tests.personas.run --base-url http://localhost:8000 --persona bachelor --timeout 300

Needs ALLOW_DEV_LOGIN=true on the server. Uses whatever model the server is configured with.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

from tests.personas.personas import PERSONAS
from tests.personas.references import REFERENCES

MAX_TURNS = 12
MAX_QUESTIONS = 8
SHOW_PLANS = "just show me plans"
SHOW_PLANS_TOKEN = "__show_plans__"  # the web app's "show me plans" button
SWIPE_CHIP = "Swipe to find my style"  # the app's swipe_service.SWIPE_CHIP
RETRIES, RETRY_WAIT = 3, 30
DEFAULT_DAYS = 4  # the app's draft_service.DEFAULT_DAYS
DRAFT_COUNT = 3  # the app's travel_planner_workflow.DRAFT_COUNT; fewer means a draft was silently dropped
CHECKED_FIELDS = ["party", "party_size", "kids", "budget_level", "start_date", "duration_days", "mood", "origin",
                  "return_to"]


class Api:
    def __init__(self, base_url: str, timeout: float):
        self.base, self.timeout, self.token = base_url.rstrip("/"), timeout, None

    def post(self, path: str, body: dict | None, method: str = "POST") -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data, headers, method=method)
        for attempt in range(RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                # 503 = the model is unavailable, usually a free-tier rate limit; wait it out a few times.
                if e.code != 503 or attempt == RETRIES:
                    raise RuntimeError(f"{path} -> HTTP {e.code}: {e.read().decode()[:200]}") from None
                print(f"  (503 from model, retrying in {RETRY_WAIT}s)", flush=True)
                time.sleep(RETRY_WAIT)


def pick_reply(persona: dict, question: str, chips: list[str], used: set[int]) -> tuple[str, dict]:
    q = question.lower()
    for i, (words, text, facts) in enumerate(persona["answers"]):
        if i not in used and any(w in q for w in words):
            used.add(i)
            return text, facts
    for chip in chips:
        if any(w in chip.lower() for w in persona["chips"]):
            return chip, {}
    return SHOW_PLANS, {}


def swipe_deck(api: Api, trip_id: str, likes: dict[str, str]) -> tuple[list[str], dict]:
    """Plays the swipe deck: each card the persona has a feeling about, "not sure" for the rest. Returns the cards
    seen and the trip after the deck's summary."""
    seen, deck = [], api.post(f"/trips/{trip_id}/swipes", None, "GET")
    while deck["card"]:
        seen.append(deck["card"]["id"])
        deck = api.post(f"/trips/{trip_id}/swipes", {"card_id": seen[-1], "answer": likes.get(seen[-1], "skip")})
    return seen, api.post(f"/trips/{trip_id}/swipes/finish", {})


def converse(api: Api, persona: dict, answer=None) -> dict:
    """Plays one persona until drafts appear (or it gives up). Returns the transcript and timings.
    answer(resp, used) -> (reply, facts it states); defaults to the scripted pick_reply (the AI agent passes its own)."""
    answer = answer or (lambda resp, used: pick_reply(persona, resp["next_question"], resp["answer_options"], used))
    # A fresh user per run: the app saves learned taste on the user, which would carry into the next run.
    email = f"persona-{persona['name']}-{time.time_ns()}@personas.test"
    api.token = api.post("/auth/dev", {"email": email})["token"]
    trip = api.post("/trips", {"title": f"persona {persona['name']}"})
    trip_id = trip["id"]
    facts = {k: list(v) for k, v in persona["facts"].items()}
    message, used = persona["opening"], set()
    turns, questions, plans_turn, vibe_due, show_plans_turn, swiped = [], 0, None, None, None, None
    for n in range(1, MAX_TURNS + 1):
        start = time.time()
        resp = api.post(f"/trips/{trip_id}/messages", {"message": message})
        turns.append({"said": message, "seconds": round(time.time() - start), "reply": resp.get("reply"),
                      "question": resp["next_question"], "chips": resp.get("answer_options") or [], "drafts": len(resp["drafts"]), "options": bool(resp.get("options"))})
        if message == SHOW_PLANS and show_plans_turn is None:
            show_plans_turn = n
        if vibe_due is None and all(k in facts for k in ("destination", "mood", "party")):
            # With the vibe known the app may still ask duration once (missing_vibe), unless it was already said,
            # then where the trip starts and whether it ends there (Ticket #81).
            vibe_due = n + (0 if "duration_days" in facts else 1) + 2
        trip = resp["trip"]
        if resp["drafts"] or resp.get("options"):
            plans_turn = plans_turn or n
        if persona.get("swipes") and swiped is None and SWIPE_CHIP in (resp.get("answer_options") or []):
            seen, trip = swipe_deck(api, trip_id, persona["swipes"])
            swiped = {"cards": seen, "summary": trip["messages"][-1]["content"], "traits": trip["personality"]["traits"]}
            message = SHOW_PLANS_TOKEN  # what the web app sends after the deck
            continue
        ideas = ((resp.get("options") or {}).get("ideas") or []) if not trip["profile"].get("destination") else []
        if ideas and persona.get("pick_idea"):
            message = f"Let's go to {ideas[0]['destination']}"  # the destination is then the persona's own choice
            facts.setdefault("destination", []).append(ideas[0]["destination"])
            continue
        if resp["drafts"] or not resp["next_question"]:
            break
        if plans_turn is None:
            questions += 1
        message, new_facts = answer(resp, used)
        for k, v in new_facts.items():
            facts.setdefault(k, []).extend(v)
    return {"trip": trip, "turns": turns, "facts": facts, "questions": questions, "plans_turn": plans_turn,
            "vibe_due": vibe_due, "show_plans_turn": show_plans_turn, "swiped": swiped}


def invented_facts(profile: dict, facts: dict) -> list[str]:
    """Profile values the persona never said. Unset is always fine."""
    bad = []
    for field in CHECKED_FIELDS:
        value, allowed = profile.get(field), facts.get(field)
        if field == "mood":
            bad += [f"mood={m!r}" for m in value or [] if not any(s in m.lower() for s in allowed or [])]
        elif value is not None and value not in (allowed or []):
            bad.append(f"{field}={value!r}")
    return bad


def pick_texts(trip: dict) -> tuple[list[tuple[str, str]], list[dict]]:
    """(label, text) for every draft and activity, plus the stay and panel picks, that the traveler would see."""
    out = []
    picks = list((trip.get("panel") or {}).get("items", []))
    for d in trip["drafts"]:
        out.append((f"draft {d['title']!r}", f"{d['title']} {d['summary']}"))
        if d["itinerary"].get("stay"):
            picks.append(d["itinerary"]["stay"])
        for day in d["itinerary"]["days"]:
            out += [(a["name"], f"{a['name']} {a.get('description') or ''}") for a in day["activities"]]
    out += [(p["name"], f"{p['name']} {p.get('summary') or ''} {p.get('why') or ''}") for p in picks]
    return out, picks


def misfit_picks(trip: dict, persona: dict) -> list[str]:
    texts, picks = pick_texts(trip)
    bad = [f"{label}: {w!r}" for label, text in texts for w in persona["banned"]
           if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
    bad += [f"{p['name']}: tier {p['tier']}" for p in picks if p.get("tier") in persona["banned_tiers"]]
    if "luxury" in persona["banned_tiers"]:  # budget travelers: no $$$$ places
        bad += [f"{p['name']}: $$$$" for p in picks if p.get("price_level") == "$$$$"]
    return sorted(set(bad))


NIGHT_OUT = re.compile(r"\b(bar|pub|club|cocktails?|wine|brewery|beer|drinks?)\b", re.I)


def meal_only_slots(trip: dict) -> list[str]:
    """Ticket #75: a meal shares its morning, afternoon or evening with a thing to do. An evening bar is the outing."""
    bad = []
    for d in trip["drafts"]:
        for day in d["itinerary"]["days"]:
            for slot in ("morning", "afternoon", "evening"):
                stops = [a for a in day["activities"] if a.get("time_of_day") == slot]
                meals = [a for a in stops if a["category"] == "eat" and not (
                    slot == "evening" and NIGHT_OUT.search(f"{a['name']} {a.get('description') or ''}"))]
                if stops and len(meals) == len(stops):
                    bad.append(f"{d['title']!r} day {day['day_number']} {slot}: {stops[0]['name']}")
    return bad


def route_gaps(trip: dict) -> list[str]:
    """Ticket #27: a country trip has a route; every city has its own stay, every move a leg, and the nights are the
    days less one."""
    gaps = []
    for d in trip["drafts"]:
        plan, title = d["itinerary"], d["title"]
        route = plan.get("route") or []
        if not route:
            gaps.append(f"{title!r} has no route")
            continue
        stays = [c["stay"]["place_id"] for c in route if c.get("stay")]
        if len(stays) < len(route) or len(set(stays)) < len(stays):
            gaps.append(f"{title!r} lacks a separate stay in each city")
        if any(not c.get("leg") for c in route[1:]):
            gaps.append(f"{title!r} lacks a leg between cities")
        if sum(c["nights"] for c in route) != len(plan["days"]) - 1:
            gaps.append(f"{title!r} nights don't add up to its {len(plan['days'])} days less one")
    return gaps


def human_plan_rules(persona: dict, trip: dict, days: int) -> list[tuple[str, bool | None, str]]:
    """Ticket #67: day count, titles against stops, repeats, rest days and the human plan's must-sees. Code only;
    it uses the app's own rules, so it runs where the app's code is (docker compose exec api ...)."""
    try:
        from app.schemas.itinerary import Itinerary
        from app.services import day_rhythm
    except ImportError:
        return [("plans read like a human's", None, "run inside the api container to check these")]
    plans = [(d["title"], Itinerary.model_validate({"destination": "", **d["itinerary"]})) for d in trip["drafts"]]
    wants = " ".join([persona.get("opening", ""), *(a[1] for a in persona.get("answers", []))])
    short = [f"{t!r} has {len(p.days)}" for t, p in plans if len(p.days) != days]
    titles = [f"{t!r} day {d.day_number} {d.theme!r}" for t, p in plans for d in p.days
              if d.theme != day_rhythm.REST_THEME and day_rhythm.promise_broken(d, {})]
    again = [f"{t!r}: {why}" for t, p in plans for _, _, why in day_rhythm.repeats(p, {}, wants)]
    busy = [f"{t!r} day {d.day_number}" for t, p in plans for d in p.days if d.theme == day_rhythm.REST_THEME
            and sum(a.category != "eat" for a in d.activities) > day_rhythm.REST_STOPS]
    rules = [(f"exactly {days} days", not short, "; ".join(short) or "every draft"),
             ("titles match the stops", not titles, "; ".join(titles) or "every title holds"),
             ("no repeated dish or too many of a sight", not again, "; ".join(again) or "none"),
             ("rest days are restful", not busy, "; ".join(busy) or "every rest day")]
    reference = REFERENCES.get(persona["name"])
    if reference and plans:
        def found(plan: Itinerary) -> list[str]:
            names = " ".join(f"{a.name} {a.description or ''}" for d in plan.days for a in d.activities).lower()
            return [m for m in reference["must_sees"] if re.search(m, names)]
        title, best = max(plans, key=lambda tp: len(found(tp[1])))
        hits = found(best)
        rules.append(("has the human plan's must-sees", len(hits) * 2 >= len(reference["must_sees"]),
                      f"{len(hits)} of {len(reference['must_sees'])} in {title!r}"))
    return rules


def judge(persona: dict, trip: dict) -> tuple[str, bool | None, str]:
    """One model call: each plan scored 1-5 against the persona's human itinerary, with a reason per score."""
    reference = REFERENCES.get(persona["name"])
    if not reference or not trip["drafts"]:
        return ("judged against a human plan", None, "no reference or no drafts")
    from pydantic import BaseModel, Field

    from app.core.config import settings
    from app.schemas.itinerary import Itinerary
    from app.services.llm_client import LLMClient
    from app.services.personality_service import PersonalityService

    class Score(BaseModel):
        reason: str = Field(description="one line, naming a stop or a day")
        score: int = Field(description="1 poor .. 5 as good as the human plan")

    class Verdict(BaseModel):
        rhythm: Score
        variety: Score
        practical_tips: Score
        reads_like_a_local: Score

    best = trip["drafts"][0]
    plan = Itinerary.model_validate(best["itinerary"])
    view = PersonalityService.plan_view(plan, None) + "\n" + "\n".join(
        f"  tip at {a.name}: {a.tip}" for d in plan.days for a in d.activities if a.tip)
    prompt = f"""Score a trip plan against a popular itinerary a person wrote for a similar trip.
The traveler: {persona['opening']}
The human plan ({reference['days']} days): {'; '.join(f'day {i}: {x}' for i, x in enumerate(reference['outline'], 1))}
Its must-sees match: {', '.join(reference['must_sees'])}. Its tips: {'; '.join(reference['tips'])}.

The plan to score:
{view}

Score each from 1 (poor) to 5 (as good as the human plan): rhythm (a main sight, an area, good meals, rest where
the pace needs it), variety (no repeated dishes or same-kind sights), practical_tips (when to go, what to book),
reads_like_a_local (the places someone who knows the destination would pick). The plan is shorter or longer
than the human one when the traveler asked so; never mark it down for that."""
    verdict = LLMClient.generate_structured(prompt, Verdict, model=settings.LLM_MODEL_PLAN)
    scores = {k: getattr(verdict, k) for k in Verdict.model_fields}
    mean = sum(s.score for s in scores.values()) / len(scores)
    return ("judged against a human plan", mean >= 3,
            f"{mean:.1f}/5 for {best['title']!r}: " + "; ".join(f"{k} {s.score} ({s.reason})" for k, s in scores.items()))


def check(persona: dict, run: dict) -> list[tuple[str, bool | None, str]]:
    """(rule, passed or None if it couldn't be checked, detail)."""
    trip, has_drafts = run["trip"], bool(run["trip"]["drafts"])
    profile = trip["profile"]
    facts = run["facts"]
    if "duration_days" not in facts:  # the app drafts with its default length when none was said
        facts = {**facts, "duration_days": [DEFAULT_DAYS]}
    invented = invented_facts(profile, facts)
    shown = {k: v for k, v in profile.items() if k in CHECKED_FIELDS and v not in (None, [])}
    rules = [("no invented facts", not invented, ", ".join(invented) or json.dumps(shown))]

    questions = run["questions"]
    rules.append((f"<= {MAX_QUESTIONS} questions before plans", run["plans_turn"] is not None and questions <= MAX_QUESTIONS,
                  f"{questions} questions" + ("" if run["plans_turn"] else ", plans never appeared")))

    drafts_turn = next((i + 1 for i, t in enumerate(run["turns"]) if t["drafts"]), None)
    due = [t for t in (run["vibe_due"], run["show_plans_turn"]) if t]
    due_turn = min(due) if due else None
    if due_turn is None:
        rules.append(("drafts once vibe is known", None, "persona never gave destination, mood and party"))
    else:
        ok = drafts_turn is not None and drafts_turn <= due_turn
        rules.append(("drafts once vibe is known", ok, f"due by turn {due_turn}, drafts at turn {drafts_turn}"))

    if has_drafts:
        bad = misfit_picks(trip, persona)
        rules.append(("picks fit the traveler", not bad, "; ".join(bad) or f"{len(pick_texts(trip)[0])} items checked"))
        lonely = meal_only_slots(trip)
        rules.append(("no meal-only slots", not lonely, "; ".join(lonely) or "every meal shares its slot"))
        rules += human_plan_rules(persona, trip, min(profile.get("duration_days") or DEFAULT_DAYS, 21))
    else:
        rules.append(("picks fit the traveler", None, "no drafts to check"))
        rules.append(("no meal-only slots", None, "no drafts to check"))
    if persona.get("multi_city"):
        broken = route_gaps(trip) if has_drafts else []
        rules.append(("a hotel per city and legs between them", not broken if has_drafts else None,
                      "; ".join(broken) or ("every draft's route is whole" if has_drafts else "no drafts to check")))
    if persona.get("swipes"):
        swiped = run.get("swiped")
        traits = " | ".join(swiped["traits"]) if swiped else ""
        # The summary line includes leanings: 12 cards can't make every scale certain.
        ok = bool(swiped) and len(swiped["cards"]) <= 12 and all(t.lower() in swiped["summary"].lower()
                                                                  for t in persona["expect_style"])
        rules.append(("style found by swiping", ok, f"{len(swiped['cards'])} cards; {swiped['summary']}; card: {traits}"
                      if swiped else "the deck was never offered"))
    n = len(trip["drafts"])
    rules.append(("all drafts arrived", n == DRAFT_COUNT if has_drafts else None, f"{n} of {DRAFT_COUNT}"))
    return rules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--persona", action="append", choices=[p["name"] for p in PERSONAS],
                        help="run only this persona (repeatable)")
    parser.add_argument("--timeout", type=float, default=600, help="seconds per HTTP turn (default 600)")
    parser.add_argument("--judge", action="store_true",
                        help="also score each persona's first plan against its human itinerary (one model call each)")
    args = parser.parse_args(argv)

    api = Api(args.base_url, args.timeout)
    selected = [p for p in PERSONAS if not args.persona or p["name"] in args.persona]
    failed = 0
    for persona in selected:
        start = time.time()
        print(f"\n== {persona['name']} ==", flush=True)
        try:
            run = converse(api, persona)
        except Exception as e:  # one broken persona shouldn't stop the rest
            failed += 1
            print(f"  ERROR  {type(e).__name__}: {e}  ({time.time() - start:.0f}s)")
            continue
        for i, t in enumerate(run["turns"], 1):
            got = f"{t['drafts']} drafts" if t["drafts"] else ("options" if t["options"] else f"asks {t['question']!r}")
            print(f"  turn {i} ({t['seconds']}s) said {t['said']!r} -> {got}")
        rules = check(persona, run) + ([judge(persona, run["trip"])] if args.judge else [])
        for rule, ok, detail in rules:
            failed += ok is False
            print(f"  {'PASS' if ok else 'FAIL' if ok is False else 'SKIP'}  {rule}: {detail}")
        card = run["trip"].get("personality") or {}
        print(f"  card: {', '.join(card.get('traits', [])) or '-'} | unknown: {', '.join(card.get('unknown', [])) or '-'}")
        for d in run["trip"]["drafts"]:
            print(f"  draft {d['title']!r} -> {d.get('trait') or 'no trait'}")
        print(f"  total {time.time() - start:.0f}s", flush=True)
    print(f"\n{failed} failure(s) across {len(selected)} persona(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
