"""Runs the six test travelers against the running app over HTTP and prints pass/fail per persona per rule.

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

MAX_TURNS = 12
MAX_QUESTIONS = 8
SHOW_PLANS = "just show me plans"
RETRIES, RETRY_WAIT = 3, 30
CHECKED_FIELDS = ["party", "party_size", "kids", "budget_level", "start_date", "duration_days", "mood"]


class Api:
    def __init__(self, base_url: str, timeout: float):
        self.base, self.timeout, self.token = base_url.rstrip("/"), timeout, None

    def post(self, path: str, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(), headers, method="POST")
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


def converse(api: Api, persona: dict) -> dict:
    """Plays one persona until drafts appear (or it gives up). Returns the transcript and timings."""
    api.token = api.post("/auth/dev", {"email": f"persona-{persona['name']}@personas.test"})["token"]
    trip = api.post("/trips", {"title": f"persona {persona['name']}"})
    trip_id = trip["id"]
    facts = {k: list(v) for k, v in persona["facts"].items()}
    message, used = persona["opening"], set()
    turns, questions, plans_turn, vibe_due, show_plans_turn = [], 0, None, None, None
    for n in range(1, MAX_TURNS + 1):
        start = time.time()
        resp = api.post(f"/trips/{trip_id}/messages", {"message": message})
        turns.append({"said": message, "seconds": round(time.time() - start), "question": resp["next_question"],
                      "drafts": len(resp["drafts"]), "options": bool(resp.get("options"))})
        if message == SHOW_PLANS and show_plans_turn is None:
            show_plans_turn = n
        if vibe_due is None and all(k in facts for k in ("destination", "mood", "party")):
            # With the vibe known the app may still ask duration once (missing_vibe), unless it was already said.
            vibe_due = n + (0 if "duration_days" in facts else 1)
        trip = resp["trip"]
        if resp["drafts"] or resp.get("options"):
            plans_turn = plans_turn or n
        if resp["drafts"] or not resp["next_question"]:
            break
        if plans_turn is None:
            questions += 1
        message, new_facts = pick_reply(persona, resp["next_question"], resp["answer_options"], used)
        for k, v in new_facts.items():
            facts.setdefault(k, []).extend(v)
    return {"trip": trip, "turns": turns, "facts": facts, "questions": questions, "plans_turn": plans_turn,
            "vibe_due": vibe_due, "show_plans_turn": show_plans_turn}


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
        bad += [f"{p['name']}: $$$$" for p in picks if p.get("price_level") == "PRICE_LEVEL_VERY_EXPENSIVE"]
    return sorted(set(bad))


def check(persona: dict, run: dict) -> list[tuple[str, bool | None, str]]:
    """(rule, passed or None if it couldn't be checked, detail)."""
    trip, has_drafts = run["trip"], bool(run["trip"]["drafts"])
    profile = trip["profile"]
    invented = invented_facts(profile, run["facts"])
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
    else:
        rules.append(("picks fit the traveler", None, "no drafts to check"))
    return rules


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--persona", action="append", choices=[p["name"] for p in PERSONAS],
                        help="run only this persona (repeatable)")
    parser.add_argument("--timeout", type=float, default=600, help="seconds per HTTP turn (default 600)")
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
        for rule, ok, detail in check(persona, run):
            failed += ok is False
            print(f"  {'PASS' if ok else 'FAIL' if ok is False else 'SKIP'}  {rule}: {detail}")
        print(f"  total {time.time() - start:.0f}s", flush=True)
    print(f"\n{failed} failure(s) across {len(selected)} persona(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
