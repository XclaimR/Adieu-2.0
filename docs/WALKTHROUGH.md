# Adieu code walkthrough

How a click in the browser reaches the database, the open place data and the LLM — feature by feature.

## Folders

| Folder | What it is |
|---|---|
| `travel-ai/` | FastAPI backend (Python), Postgres models, migrations, tests, `docker-compose.yml` |
| `travel-ai-web/` | React + Vite frontend, served by nginx in Docker |
| `docs/` | This file |

## Running pieces

```
browser ──► nginx (travel-ai-web, :8080) ──/api/*──► FastAPI (travel-ai, :8000) ──► Postgres (:5433)
                │ serves the built React app                 ├──► open place data, once per destination:
                                                             │    Wikidata, Wikivoyage, Commons (Wikimedia APIs, 1 req/s)
                                                             │    Overture Maps Parquet files on S3 (DuckDB)
                                                             └──► LLM via OpenAI-compatible API (Ollama qwen3:8b on the host)
```

- nginx strips `/api` and forwards to the API container (`travel-ai-web/nginx.conf`).
- Every LLM call goes through `LLMClient.generate_structured` (`travel-ai/app/services/llm_client.py`): it asks for JSON matching a Pydantic schema, retries once on invalid JSON, and once on timeouts/connection errors.
- Every place lookup goes through `PlacesService` (`app/services/places_service.py`). It keeps each destination in
  the `place_cache` table: its area (`open:area:<id>`, 180 days) and its library of places (`open:places:<id>`,
  90 days, an hour if a source was down). A normal turn reads only Postgres.
  - `gazetteer.py`: destination name → area. Countries from `app/data/countries.tsv` (offline); anything else from
    Wikidata (search, then coordinates, extreme points or size). Never a business. "Georgia, USA" and a saved trip's
    box narrow ambiguous names.
  - `wikivoyage.py`: the destination's guide, its district pages and guides in reach → described places (see / do /
    eat / drink / sleep listings; stay tiers from Budget / Mid-range / Splurge headings) and the main towns.
    `open_data.safe_text` turns downloaded text into one short plain line; sentences with prices or hours are dropped.
  - `overture.py`: places by bounding box from Overture's files, the whole area when small, else around the main towns.
  - `place_library.py`: merges both (a described place takes Overture's position and type), caps 450 / 300 / 170.
  - Choosing (`PlacesService.candidates`): words from the query, the notes and the mood; "near Alfama" sorts by
    distance; big areas take turns between their main towns. `FACTS_RULE` and `unclaimed()` keep the model from
    claiming crowds, prices, ratings or opening times the data doesn't state.

## Backend layers (`travel-ai/app/`)

| Layer | Files | Job |
|---|---|---|
| Entry | `main.py` | Creates the app, CORS, mounts routers, turns LLM errors into friendly messages |
| API routes | `api/auth.py`, `api/me.py`, `api/trips.py`, `api/deps.py` | HTTP endpoints; `deps.current_user` checks the bearer token |
| Workflow | `workflows/travel_planner_workflow.py` | The brain of a trip: one chat turn, choosing drafts, likes/skips, swaps |
| Services | `services/*.py` (live ones listed below) | One job each: LLM prompts, place data, ranking, drafts |
| Repositories | `repositories/trip_repository.py`, `user_repository.py` | All database reads/writes |
| Models | `database/models/trip.py`, `user.py`, `place_cache.py` | SQLAlchemy tables: users, trips, messages, pick_feedback, place_cache |
| Schemas | `schemas/*.py` | Pydantic shapes for API bodies and LLM outputs |
| Config | `core/config.py`, `core/security.py` | Env settings; signing/reading session tokens (JWT) |
| Migrations | `migrations/versions/0001…`, `0002…` | Run by `alembic upgrade head` when the API container starts |

## Feature by feature

### Sign in
`screens/Login.tsx` → `GET /auth/config` (shows Google button and/or dev form) →
`POST /auth/google` (verifies Google ID token) or `POST /auth/dev` (only when `ALLOW_DEV_LOGIN=true`) →
`UserRepository.upsert` → `core/security.create_session_token` → token saved in `localStorage` by `api.ts:setToken`.
Every later request sends it as `Authorization: Bearer …`, checked by `api/deps.current_user`.

### Vibe picker ("My vibe")
`screens/VibePicker.tsx` → `GET /vibes` (fixed list in `services/taste_service.VIBES`) →
`POST /me/taste` → `TasteService.set_vibes` → stored on the user as their taste.

### Home
`screens/Home.tsx`
- Trip list: `GET /trips` → `TripRepository.list_for_user` (title = chosen plan's name, length, destination photo).
- Ideas for you: `GET /starter-ideas` → `api/me.starter_ideas` (24h cache) → `StarterIdeaService.generate` (LLM suggests countries from your taste; the offline country table drops anything else, and repeats).
- "Plan it" / "Surprise me" / idea card: `POST /trips` creates an empty trip, then the Trip screen sends what you typed as the first message.

### A chat turn (the core loop)
`screens/Trip.tsx:send` → `api.ts:sendStream` → `POST /trips/{id}/messages/stream` (streams NDJSON events) →
`TravelPlannerWorkflow.run_turn`:

"reset" or "start over" as the whole message never reaches the model: the workflow asks "Are you sure?" and deletes
only on a clear yes while that is the current question. A no brings back the question from before. Otherwise:
1. `TurnService.decide` — LLM reads the message + history and returns a `TurnDecision` (core facts, note changes, reply, intent). Its schema only allows note targets that exist on this trip.
2. `TripProfileService.merge` + `grounding.ground_updates` — keeps only facts the user actually said (e.g. `extract_duration` turns "a slow week" into 7 days).
3. New destination? `PlacesService.resolve_destination` checks it's a real place (not a business) and stores its map
   area, with the area's id inside the saved box. Its places are loaded the first time plans or options need them (15-30 s).
   Then `PersonalityService.update` refreshes the trip's **personality** (`trips.personality`). It is about this
   trip only: lasting taste is not read or written during a trip (it still feeds the Home ideas), except the home
   city below.
   - **Where the trip starts and ends** (Ticket #81): after the vibe, `TurnService` asks once "Which city are you
     starting from?", then "Flying back to <it> at the end, or a different city?". The answer is saved as lasting
     taste (`Taste.home`), so later trips ask only "Starting from <home> and flying back there?" with one-tap chips.
     `read_ends` reads the chips and short typed answers in code; the model reads longer ones into
     `profile.origin` / `return_to`, and the gazetteer checks them. Skipped, they default to the home city and back.
     These questions count toward the cap of 8 questions before plans (`MAX_QUESTIONS`).
   - **Notes** are everything the traveler said beyond the core facts. Each is stored once and points at what it
     is about: `trip`, a day (`d2`), the stay (`s1`) or an activity's id. It has a kind (must / avoid / want), a
     category, and is stated or inferred. The model does the understanding (`TurnDecision.note_changes`: add,
     remove, or do = one-time); `check_changes` checks only structure: real quote, real ids. Nothing is overwritten.
   - Targets are slots, so a note survives swapping the place. A removed stop's notes move to its day.
   - **Mood** and the **six scales** are derived from the notes (plus budget and kids from the profile).
   - **Plan scales** (Ticket #83, `services/plan_scales.py`): rules read a plan as steps 1–5 on each scale and $ to
     $$$$$, worked out on read in `TripRepository.to_out`, so edits move them with no stored state. Energy and cost
     per stop come from the critic's existing per-stop ratings (`Activity.energy/cost`), with keyword fallbacks.
     The ghosts are the stated scales (confidence ≥ 0.5); the old cheap/splurge `money` lean is the $ ghost.
   `traveler_fit` (places_service) gives ranking and drafts the stated limits, the wishes and the numbered moments
   ("one big night out"); choosing a draft points each moment's note at the stop that fulfils it.
   Live check: `python -m tests.evals.run_personality_evals` (17 messages, one call each).
4. `TurnService.plan` — rules decide what happens next: ask a question, show options, offer drafts, or edit the itinerary.
5. The reply is streamed and **saved immediately**, then the slow part runs:
   - Show options → `OptionsService.build` → the destination's places (`PlacesService.candidates`) → `RankingService` (LLM ranks by the trip's notes) → slide-in panel (`components/OptionsPanel.tsx`, `PickCard.tsx`).
   - Multi-city routes (Ticket #27): for a country or region, `DraftService.route` → `RouteService.plan`
     (`services/route_service.py`) first picks the bases. Code finds the towns with enough listed places (a town in
     reach of a better-known one is a day trip, not a base), decides how many cities fit the days and the pace (about
     3 days a city; slow 4, packed 2), orders them as the shortest one-way route counted from the main city, where
     flights usually land, and shares the days so they add up to the trip. Nights are strict: the days less one.
     With a home city (Ticket #81) the model also names the listed towns with international flights, and the order
     counts the trip from home and back: a quarter of a ground km per km flown, plus the ride between an end city
     and its airport. So it runs one way, open-jaw where that helps (land in Tokyo, leave from Osaka), whatever order
     the model listed, and may end in a city without an airport a ride from one (Kyoto, then the train to Osaka).
     `route_service.flights` gives the way in and out (`Itinerary.fly_in` / `fly_out`, kept for booking links): by
     land when home is close, else a flight with the times airlines quote, with that ride as `transfer`; an end far
     from every known airport flies from the city itself. One-place trips get the same, to the place itself. Day 1
     and the last day are light, in the prompt and the review. The card shows home at both ends of the line, the
     flights dashed; a one-place trip gets a "Getting there" card.
     One model call picks which towns suit this traveler and estimates each leg (mode, rough fare, a pass tip); then
     Transitous (api.transitous.org, an open, free public-transport router; light non-commercial use) gives the real
     journey time where it has timetables and the ride is under 8 hours. Without them, code uses the best-known towns
     and a distance-based estimate. Another round of drafts keeps the route while the days and pace are the same.
     A city with no listed hotel links an OpenStreetMap hotel search (Booking.com's API needs a partner contract). Each city gets its own pool of nearby places, the draft is told
     which days are in which city and that moving days are light, and code drops a stop outside its day's city and
     gives every city a stay (`Itinerary.route`, stays `s1`, `s2`, ...; `DayPlan.city`). The review flags a stop in
     another city, a missing stay per city and a busy moving day, and a moving day's clock starts when the train gets
     in. Edits, repairs, swaps and ＋ keep places in their city. The board shows it as `components/RouteCard.tsx`.
   - Offer drafts → `DraftService.generate` → one pool of places (name, type, area, description), then one LLM call per flavor (easy / classic / local) → `DraftService.review` per plan → `DraftCard.tsx`, each shown once its own review is done.
     Until the first reviewed draft arrives the reply is only "Let me put some plans together"; then a second `reply` event replaces it
     with the model's words and "Here are 3 ways". With no draft (too few places, so no LLM call; place data or the LLM
     unavailable; all drafts rejected) the reply and the panel say why, from `trip.state.no_plans`.
   - Human-quality plans (Ticket #67, `services/day_rhythm.py`). Fame: Wikivoyage listings keep their Wikidata id
     and the library stores how many Wikipedias cover each place (`wikivoyage.fame`, one Wikidata call per 50
     places; Fushimi Inari 39, the Osaka Science Museum 8), which adds to its score. Libraries built before carry no
     `version` and are rebuilt once. Must-sees are each city's most famous sights (15+ Wikipedias, up to days + 1);
     a city with 3+ nights gets a day trip to the most famous town nearby (Nara from Kyoto, Miyajima from Hiroshima).
     One model call per trip (`DraftService._check_must_sees`) drops odd picks, adds when to go or what to book,
     and, only on a strong "avoid crowds", names a quieter stand-in, shown as "Instead of ...". Hidden gems are
     described places under 10 Wikipedias. Tips come from the guide's own sentences (book, early, sunset, closed
     days; no prices) or that call; a stop follows its tip (early first in the morning, sunset in the evening) and
     shows it as a line under its note. Pace sets stops a day (slow 3 .. packed 5; drafting asks for at most 4,
     the review adds a fifth) and rest days (`day_rhythm.roles`: slow every 3-4 days, middle once a week on 7+
     days, none when packed), named "Rest day" by code: at most 2 calm places (critic energy 0; meals exempt), no
     night out. Code also flags a dish twice a day or more than max(2, days/4) times a trip and 3 sights of a kind
     a day, unless asked for; a title that promises a beach or a club the day lacks becomes "main stop and area";
     a day nothing could fill stays, reported as empty, so the trip keeps its length. A rest day needs one calm
     place, not only cafés or bars. A sunrise tip is an early start. Junk listings ("in front of Amed
     Cafe", a town's own name) are dropped when the library loads.
   - Every destination has a real area (atolls and mountain ranges too). Trips saved with Google's area, which could
     be a 300 m box around a pin, are looked up again by name near that box.
   - Edit itinerary → `DraftService.edit`: the model gets the changed part with its notes and returns edit
     operations (replace / remove / add / move); code applies them and leaves everything else untouched.
     `LLM_MODEL_PLAN` can name a stronger model for drafts, edits and their review (default: `LLM_MODEL`).
   - Review, before the traveler sees a plan or an edit (`DraftService._check_and_fix`, one plan at a time). Each
     draft's review starts in a thread as soon as it is written, so it overlaps writing the next
     (`TravelPlannerWorkflow._offer_drafts`); only the workflow thread touches the database.
     1. `ItineraryCritiqueService.critique` is one LLM call per plan (one call for three plans mixed them up, live).
        It must give a verdict per rule: destination, travel style, activity balance, purpose, logic, budget (only if stated), and one
        rule per stated note. A failure that points at nothing real is ignored. The same call estimates the
        minutes needed at every stop; they are saved on each stop (`Activity.minutes`) and shown as its time chip.
        A stop nobody estimated (older plans, swaps, a failed critic) takes a default: 75 min to eat, else 90.
     2. `ItineraryCritiqueService.counted` is plain code: day count, empty or one-stop days, a missing stay, and
        whether each day is doable. For that it walks the day's clock from the stay, adding the visit minutes and
        an estimated travel time (straight line plus 30%: walking, transit or road; no routing API), and flags
        a stop that can't start in its morning / afternoon / evening, a day past 1 am, or over 4 hours of travel.
        It also flags a morning, afternoon or evening with only a meal in it (Ticket #75): a meal shares its slot
        with a thing to do; an evening bar counts as the outing. Drafts, edits and repairs are told the same rule.
        Stops keep their `lat`/`lng` for this. The model never sees these results, so it can't parrot them.
     3. One repair call. It returns the same edit operations, and code lets them touch only what was flagged. After
        an edit that is only what the edit changed, and a repair can't remove a stop. A repair that leaves more
        countable errors is thrown away.
     4. One re-check of only what the repair changed (the same critic, scoped like an edit). If it finds a problem,
        or fails, the plan goes back to how it was before the repair and the original problems are shown. No loop:
        repairs can chase each other and each round costs 3-50 s.
     5. What is still wrong shows on the card ("Check before choosing") or after the edit ("Worth a look").
        If the critic fails or times out (45 s per attempt), the plan still shows, marked "I couldn't double-check
        this plan." Cost: a drafting turn is 8 LLM calls (9 with a route; one is the must-see check), up to 13 with repairs and re-checks (under the free
        tier's 15 a minute); an edit is 3, up to 5.
6. A final `done` event carries the refreshed trip.

Events the browser handles: `progress` (step list), `reply`, `note`, `options`, `draft`, `done`, `error` (shown with a **Try again** button if the message wasn't saved).

### Swipe deck ("Not sure? Swipe to find your style")
For travelers who can't describe their trip. Home's "Not sure? Swipe…" button, or the "Swipe to find my style" chip,
opens `components/SwipeDeck.tsx` (own `SwipeDeck.css`; a full-screen sheet on phones). No LLM calls:
`GET /trips/{id}/swipes` → next card, `POST /trips/{id}/swipes` {card_id, answer: right/left/skip} → saves the answer
in `trip.state["swipes"]` and the scales, `POST /trips/{id}/swipes/finish` → says "You lean: …" in the chat and sets the
trip's mood from the cards swiped right if none was said; the client then sends "show me plans" as usual (`api/swipes.py`).
- `services/swipe_service.py`: 15 cards with fixed, tested effects on the six scales. Left counts half of right;
  disagreeing swipes cancel out (lower confidence, never a flip). Swiped scales (source `swiped`) top out at 0.6, below
  anything said, and only fill scales nothing surer set. Next card = the one touching the least-known scales; 12 at most.
- Offered (`swipe_service.offer`) when the model says the traveler is stuck, they say "not sure" / "surprise me" and the
  like, or the mood question is asked a second time; on every new trip's first question too; never once used or with a plan.
- Photos: Wikimedia Commons, openly licensed, stored in `travel-ai-web/public/swipe/`, credited on the card.

### Picking a plan, likes and edits
| UI | Endpoint | Workflow method |
|---|---|---|
| "Choose this plan" (`DraftCard`) | `POST /trips/{id}/drafts/{draft_id}/choose` | `choose_draft` |
| ♡ / ＋ / skip on a place (`PickCard`) | `POST /trips/{id}/picks/{place_id}` | `pick_action` (a like becomes an inferred note on this trip; liking a pricey stay suggests cheaper ones) |
| Swap / Remove on an activity (`ItineraryView`) | `POST /trips/{id}/itinerary/days/{d}/activities/{i}` | `activity_action` |
| Picking a replacement in the panel | `…/activities/{i}/replace` | `replace_activity` |

### Photos
`components/Photo.tsx` → `GET /photos/commons/<id>` (no login, so `<img>` works) → `PlacesService.photo` → redirect to
the Wikimedia Commons image. `GET /photo-credits/…` gives the author and licence shown on the photo. Only openly
licensed photos (no NC/ND) are kept. Photos saved from Google answer 404 and show the placeholder.
Credits for all sources are under "Data credits" at the bottom of every page.

## Frontend map (`travel-ai-web/src/`)

| File | Role |
|---|---|
| `main.tsx`, `App.tsx` | Boot, top bar, which screen is showing |
| `api.ts` | Every backend call; token handling; stream reader |
| `types.ts` | TypeScript mirrors of the backend schemas |
| `screens/` | `Login`, `VibePicker`, `Home`, `Trip` |
| `components/` | `DraftCard` (+ loading skeleton), `IdeaCard`, `PickCard`, `OptionsPanel`, `ItineraryView` |
| `styles.css` | All styling; colors are tokens at the top (`--ink`, `--accent`, `--primary`, …) |

## Tests (`travel-ai/tests/`)
- `tests/v1/` — the live code: API flows with a fake LLM and fake open data (`conftest.py`), grounding, ranking, parsing and merging the open data (`test_open_data.py`).
  Run: `docker compose exec api python -m pytest -q --ignore=tests/evals`
- `tests/evals/` — real-model conversation checks: `docker compose exec api python -m tests.evals.run_turn_evals`
- `tests/personas/` — ten test travelers (bachelor group to Japan, Lisbon food couple, family beach, solo hiker,
  budget backpacker, luxury weekend, "unsure", who plays the swipe deck and picks the first idea, and from
  Ticket #67 a slow wellness trip to Bali, a first-timer in Japan and a family in Rome) chat with the **running** app over HTTP and get pass/fail per rule: no invented
  profile facts, at most 8 questions before plans, drafts once destination/mood/party are known, picks fit the
  traveler. Personas and their scripted answers live in `personas.py`; the checks in `run.py`. Needs
  `ALLOW_DEV_LOGIN=true`; uses whatever model the server is configured with (local qwen3:8b takes a few minutes per
  persona). Exits non-zero on any failure. Not part of the pytest run.
  - All six, inside the container (after a rebuild so the image has the folder): `docker compose exec api python -m tests.personas.run`
  - From the host against any server (stdlib only): `cd travel-ai && python3 -m tests.personas.run --base-url http://localhost:8000`
  - One persona, longer per-turn timeout: `--persona bachelor --timeout 900` (`--persona` repeats)
  - Also checks all 3 drafts arrived (a dropped draft fails).
  - Ticket #67, inside the container: exact day count, titles against stops, repeats, restful rest days, and how many
    of a human itinerary's must-sees a draft has (`references.py`: our own summaries of seven owner-approved
    itineraries; none for bachelor, family_beach, unsure). `--judge` adds one model call per persona that scores the
    first plan 1-5 against it on rhythm, variety, tips and "reads like a local".
- `tests/agent/` — AI traveler agent: a model plays each persona through the running app in its own words (taps
  chips, says "just show me plans", picks a draft), then a judge model gives each persona rule a reason, adds
  "no needless questions" and "notes match what was said", and scores "felt understood" 1–5. Reuses the personas and
  rule checks above; the checks decide their own rules, the judge decides those two. It waits 60s between personas so
  the test doesn't trip a 15-a-minute limit itself. It stops at the chosen draft: edits are not exercised. Uses the app's LLM settings, so run it inside an api container:
  `docker compose exec api python -m tests.agent.run [--persona NAME] [--report agent-report.md]`. About 4 model calls
  per persona on top of the app's own (about 15 more, with the critic); waits out 429/503 a few times. With open place
  data stays rarely have a price tier, so the budget checks in "picks fit the traveler" seldom have anything to check. Sample: `docs/reports/agent-report-sample.md`.
- `.claude/skills/traveler/` — `/traveler [local url] [who the traveler is]` in Claude Code: Claude plays a random
  person in the browser, seeing only the screen, and plans the way people think: where, weather and best time, how
  long, what's of interest, hidden gems, route, where to stay, cost. It reports which of those Adieu answered.
  Exploratory, so it varies per run (the scripted agent above is the repeatable check). A walk saves a test trip
  and spends that app's model quota.
