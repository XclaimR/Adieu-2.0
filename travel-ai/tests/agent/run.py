"""AI traveler agent: a model plays each persona through the running app over HTTP (answers in its own words, taps
chips, says "just show me plans", picks a draft), then a judge model scores the transcript against the persona rules.

Runs where the app's code and LLM settings are (the agent and judge use the app's model config), e.g. your own
api container:
    docker exec adieu-api-18 python -m tests.agent.run --base-url http://localhost:8000 --persona bachelor
    docker compose exec api python -m tests.agent.run --report agent-report.md

Needs ALLOW_DEV_LOGIN=true on the server. Per persona: one model call per agent turn, one to pick a draft, one judge.
Prints the report and writes it as markdown. Exits non-zero on any failed rule.
"""
import argparse
import sys
import time

from openai import APIStatusError
from pydantic import BaseModel, Field

from app.services.llm_client import LLMClient
from tests.personas.personas import PERSONAS
from tests.personas.run import SHOW_PLANS, Api, check, converse

LLM_TRIES, LLM_WAIT = 4, 30
JUDGE_ONLY_RULE = "no needless questions"

AGENT_SYSTEM = """You are a traveler chatting with a trip-planning app. Stay in character and write like a person
texting: short, casual, one or two sentences, in your own words (don't copy the sheet word for word).
Only state facts from your character sheet. Never add facts you weren't given (group size, dates, ages, budget...).
If one of the offered chips fits what you'd say, reply with exactly that chip's text.
In used_answers list every numbered sheet line your reply gives (even in other words or via a chip).
If the question is about something not on your sheet, or you'd rather just see plans now, reply exactly
"just show me plans"."""

JUDGE_SYSTEM = """You judge a trip-planning app from one test conversation with a traveler. Be strict and brief.
Give a verdict for EVERY rule listed, using its exact name. For each give passed (true/false) and one short sentence of reason that points at the transcript.
Automatic check results are given; disagree only if the transcript clearly shows they are wrong.
"no needless questions": fails if the app asked about something the traveler had already said or clearly implied
(e.g. asking the kind of trip after "a hiking trip"), or asked the same thing twice. Asking about something only hinted at is fine (a budget like "luxury" is not
a trip mood).
felt_understood (1-5): from the traveler's side, did the app get who they are and what trip they want?
Give one sentence why."""


class AgentReply(BaseModel):
    reply: str
    used_answers: list[int] = Field(description="numbers of the 'things you can say' that your reply states; [] if none")


class DraftPick(BaseModel):
    draft: int = Field(description="number of the plan you pick")
    why: str = Field(description="one sentence, in character")


class RuleVerdict(BaseModel):
    rule: str
    passed: bool
    reason: str


class Judgement(BaseModel):
    rules: list[RuleVerdict] = Field(description="one per listed rule, all of them, exact names")
    felt_understood: int = Field(description="1 to 5")
    felt_understood_why: str


def ask_llm(prompt: str, schema: type[BaseModel], system: str):
    """The app's own LLM client, with a bounded wait when the shared free tier is busy (429/503)."""
    for attempt in range(LLM_TRIES):
        try:
            return LLMClient.generate_structured(prompt, schema, system=system)
        except APIStatusError as e:  # RateLimitError is one of these
            if e.status_code not in (429, 503) or attempt == LLM_TRIES - 1:
                raise
        print(f"  (agent/judge model busy, retrying in {LLM_WAIT}s)", flush=True)
        time.sleep(LLM_WAIT)


def sheet(persona: dict) -> str:
    lines = [f'You opened with: "{persona["opening"]}"', "Things you can say if asked:"]
    lines += [f"{i}. ({words[0]}) {text}" for i, (words, text, _) in enumerate(persona["answers"])]
    lines += [f"Chips you'd tap if offered: anything about {', '.join(persona['chips'])}",
              f"You'd dislike plans with: {', '.join(persona['banned'])}"]
    return "\n".join(lines)


def facts_for(persona: dict, indices: list[int], used: set[int]) -> dict:
    """Profile facts the reply stated, from the sheet answers the agent says it used (each counts once)."""
    facts = {}
    for i in indices:
        if 0 <= i < len(persona["answers"]) and i not in used:
            used.add(i)
            for k, v in persona["answers"][i][2].items():
                facts.setdefault(k, []).extend(v)
    return facts


def agent(persona: dict):
    history = [f"You: {persona['opening']}"]

    def answer(resp: dict, used: set[int]) -> tuple[str, dict]:
        history.append(f"App: {resp.get('reply') or ''} {resp['next_question']}".strip())
        prompt = (f"{sheet(persona)}\n\nConversation so far:\n" + "\n".join(history)
                  + f"\n\nChips offered: {resp['answer_options'] or 'none'}\nYour reply?")
        out = ask_llm(prompt, AgentReply, AGENT_SYSTEM)
        text = SHOW_PLANS if SHOW_PLANS in out.reply.lower() else out.reply.strip()
        history.append(f"You: {text}")
        return text, facts_for(persona, out.used_answers, used)
    return answer


def pick_draft(api: Api, persona: dict, trip: dict) -> dict | None:
    if not trip["drafts"]:
        return None
    listing = "\n".join(f"{i}. {d['title']}: {d['summary']}" for i, d in enumerate(trip["drafts"]))
    out = ask_llm(f"{sheet(persona)}\n\nThe app offers these plans:\n{listing}\n\nWhich one do you pick?",
                  DraftPick, AGENT_SYSTEM)
    d = trip["drafts"][min(max(out.draft, 0), len(trip["drafts"]) - 1)]
    api.post(f"/trips/{trip['id']}/drafts/{d['id']}/choose", {})
    return {"title": d["title"], "why": out.why}


def transcript(run: dict) -> str:
    lines = []
    for t in run["turns"]:
        lines.append(f"Traveler: {t['said']}")
        got = f"{t['drafts']} drafts" if t["drafts"] else f"asks {t['question']!r}, chips {t['chips']}"
        lines.append(f"App: {t['reply']} -> {got}")
    return "\n".join(lines)


def judge(persona: dict, run: dict, rules: list, choice: dict | None) -> Judgement:
    status = {True: "PASS", False: "FAIL", None: "not checked"}
    trip = run["trip"]
    drafts = "\n".join(f"- {d['title']}: {d['summary']}" for d in trip["drafts"]) or "none"
    prompt = (f"Traveler's character sheet:\n{sheet(persona)}\n\nConversation:\n{transcript(run)}\n\n"
              f"Trip profile the app stored: {trip['profile']}\nDrafts offered:\n{drafts}\n"
              f"Traveler picked: {choice or 'nothing'}\n\nRules:\n"
              + "\n".join(f"- {r}: automatic check {status[ok]} ({d})" for r, ok, d in rules)
              + f"\n- {JUDGE_ONLY_RULE}: no automatic check")
    return ask_llm(prompt, Judgement, JUDGE_SYSTEM)


def merge(rules: list, judgement: Judgement) -> list[dict]:
    """One row per rule. The automatic check decides where it ran; the judge decides the rest and adds reasons."""
    by_name = {v.rule.strip().lower(): v for v in judgement.rules}
    rows = []
    for name, ok, detail in [*rules, (JUDGE_ONLY_RULE, None, "judged from the transcript")]:
        v = by_name.get(name.lower())
        rows.append({"rule": name, "passed": ok if ok is not None else (v.passed if v else None), "detail": detail,
                     "judge": v.passed if v else None, "reason": v.reason if v else "no verdict from the judge"})
    return rows


def mark(ok: bool | None) -> str:
    return {True: "PASS", False: "**FAIL**", None: "SKIP"}[ok]


def section(persona: dict, run: dict, choice: dict | None, rows: list[dict], judgement: Judgement) -> str:
    out = [f"## {persona['name']}", "", "| # | Traveler said | App |", "|---|---|---|"]
    for i, t in enumerate(run["turns"], 1):
        got = f"{t['drafts']} drafts" if t["drafts"] else f"asks *{t['question']}*"
        out.append(f"| {i} | {t['said']} | {got} ({t['seconds']}s) |")
    out += ["", f"Picked: {choice['title']!r} — {choice['why']}" if choice else "Picked: nothing (no drafts)", "",
            "| Rule | Result | Check | Judge | Judge's reason |", "|---|---|---|---|---|"]
    out += [f"| {r['rule']} | {mark(r['passed'])} | {r['detail']} | {mark(r['judge'])} | {r['reason']} |" for r in rows]
    score = max(1, min(5, judgement.felt_understood))
    out += ["", f"Felt understood: **{score}/5** — {judgement.felt_understood_why}", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--persona", action="append", choices=[p["name"] for p in PERSONAS],
                        help="run only this persona (repeatable)")
    parser.add_argument("--timeout", type=float, default=600, help="seconds per HTTP turn (default 600)")
    parser.add_argument("--report", default="agent-report.md", help="markdown report path")
    args = parser.parse_args(argv)

    api = Api(args.base_url, args.timeout)
    selected = [p for p in PERSONAS if not args.persona or p["name"] in args.persona]
    sections, failed = [], 0
    for persona in selected:
        start = time.time()
        print(f"\n== {persona['name']} ==", flush=True)
        try:
            run = converse(api, persona, agent(persona))
            choice = pick_draft(api, persona, run["trip"])
            rules = check(persona, run)
            judgement = judge(persona, run, rules, choice)
        except Exception as e:  # one broken persona shouldn't stop the rest
            failed += 1
            sections.append(f"## {persona['name']}\n\nERROR {type(e).__name__}: {e}\n")
            print(sections[-1])
            continue
        rows = merge(rules, judgement)
        failed += sum(r["passed"] is False for r in rows)
        sections.append(section(persona, run, choice, rows, judgement))
        print(sections[-1], f"total {time.time() - start:.0f}s", sep="\n", flush=True)
    summary = f"{failed} failure(s) across {len(selected)} persona(s)"
    with open(args.report, "w") as f:
        f.write(f"# Traveler agent report\n\n{summary}\n\n" + "\n".join(sections))
    print(f"\n{summary}\nreport: {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
