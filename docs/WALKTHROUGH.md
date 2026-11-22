# Adieu code walkthrough

How a click in the browser reaches the database, Google Places and the LLM — feature by feature.

## Folders

| Folder | What it is |
|---|---|
| `travel-ai/` | FastAPI backend (Python), Postgres models, migrations, tests, `docker-compose.yml` |
| `travel-ai-web/` | React + Vite frontend, served by nginx in Docker |
| `docs/` | This file |

## Running pieces

```
browser ──► nginx (travel-ai-web, :8080) ──/api/*──► FastAPI (travel-ai, :8000) ──► Postgres (:5433)
                │ serves the built React app                 ├──► Google Places API (search, photos)
                                                             └──► LLM via OpenAI-compatible API (Ollama qwen3:8b on the host)
```

- nginx strips `/api` and forwards to the API container (`travel-ai-web/nginx.conf`).
- Every LLM call goes through `LLMClient.generate_structured` (`travel-ai/app/services/llm_client.py`): it asks for JSON matching a Pydantic schema, retries once on invalid JSON, and once on timeouts/connection errors.
- Every Places call goes through `PlacesService` (`app/services/places_service.py`), which caches results in the `place_cache` table (searches 7 days, photo URLs 12 hours, home ideas 24 hours).

## Backend layers (`travel-ai/app/`)

| Layer | Files | Job |
|---|---|---|
| Entry | `main.py` | Creates the app, CORS, mounts routers, turns LLM errors into friendly messages |
| API routes | `api/auth.py`, `api/me.py`, `api/trips.py`, `api/deps.py` | HTTP endpoints; `deps.current_user` checks the bearer token |
| Workflow | `workflows/travel_planner_workflow.py` | The brain of a trip: one chat turn, choosing drafts, likes/skips, swaps |
| Services | `services/*.py` (live ones listed below) | One job each: LLM prompts, Places, ranking, drafts |
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
- Ideas for you: `GET /starter-ideas` → `api/me.starter_ideas` (24h cache) → `StarterIdeaService.generate` (LLM suggests countries from your taste, then Places drops anything that isn't a country and adds a photo; none are shown while Places is down).
- "Plan it" / "Surprise me" / idea card: `POST /trips` creates an empty trip, then the Trip screen sends what you typed as the first message.

### A chat turn (the core loop)
`screens/Trip.tsx:send` → `api.ts:sendStream` → `POST /trips/{id}/messages/stream` (streams NDJSON events) →
`TravelPlannerWorkflow.run_turn`:
1. `TurnService.decide` — LLM reads the message + history and returns a `TurnDecision` (core facts, note changes, reply, intent). Its schema only allows note targets that exist on this trip.
2. `TripProfileService.merge` + `grounding.ground_updates` — keeps only facts the user actually said (e.g. `extract_duration` turns "a slow week" into 7 days).
3. New destination? `PlacesService.resolve_destination` checks it's a real place (not a business) and stores its map area.
   Then `PersonalityService.update` refreshes the trip's **personality** (`trips.personality`). It is about this
   trip only: lasting taste is not read or written during a trip (it still feeds the Home ideas).
   - **Notes** are everything the traveler said beyond the core facts. Each is stored once and points at what it
     is about: `trip`, a day (`d2`), the stay (`s1`) or an activity's id. It has a kind (must / avoid / want), a
     category, and is stated or inferred. The model does the understanding (`TurnDecision.note_changes`: add,
     remove, or do = one-time); `check_changes` checks only structure: real quote, real ids. Nothing is overwritten.
   - Targets are slots, so a note survives swapping the place. A removed stop's notes move to its day.
   - **Mood** and the **six scales** are derived from the notes (plus budget and kids from the profile).
   `traveler_fit` (places_service) gives ranking and drafts the stated limits, the wishes and the numbered moments
   ("one big night out"); choosing a draft points each moment's note at the stop that fulfils it.
   Live check: `python -m tests.evals.run_personality_evals` (17 messages, one call each).
4. `TurnService.plan` — rules decide what happens next: ask a question, show options, offer drafts, or edit the itinerary.
5. The reply is streamed and **saved immediately**, then the slow part runs:
   - Show options → `OptionsService.build` → Places search in the destination → `RankingService` (LLM ranks by the trip's notes) → slide-in panel (`components/OptionsPanel.tsx`, `PickCard.tsx`).
   - Offer drafts → `DraftService.generate` → one Places pool, then one LLM call per flavor (easy / classic / local) → `DraftCard.tsx`, arriving one by one.
   - Edit itinerary → `DraftService.edit`: the model gets the changed part with its notes and returns edit
     operations (replace / remove / add / move); code applies them and leaves everything else untouched.
     `LLM_MODEL_PLAN` can name a stronger model for drafts and edits (default: `LLM_MODEL`).
6. A final `done` event carries the refreshed trip.

Events the browser handles: `progress` (step list), `reply`, `note`, `options`, `draft`, `done`, `error` (shown with a **Try again** button if the message wasn't saved).

### Picking a plan, likes and edits
| UI | Endpoint | Workflow method |
|---|---|---|
| "Choose this plan" (`DraftCard`) | `POST /trips/{id}/drafts/{draft_id}/choose` | `choose_draft` |
| ♡ / ＋ / skip on a place (`PickCard`) | `POST /trips/{id}/picks/{place_id}` | `pick_action` (a like becomes an inferred note on this trip; liking a pricey stay suggests cheaper ones) |
| Swap / Remove on an activity (`ItineraryView`) | `POST /trips/{id}/itinerary/days/{d}/activities/{i}` | `activity_action` |
| Picking a replacement in the panel | `…/activities/{i}/replace` | `replace_activity` |

### Photos
`api.ts:photoUrl` → `GET /photos/{places/…/photos/…}` (no login, so `<img>` works) → `PlacesService.photo_url` (strict name check) → redirect to Google's short-lived image URL. The API key never reaches the browser.

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
- `tests/v1/` — the live code: API flows with a fake LLM and fake Places (`conftest.py`), grounding, ranking, photo-name safety.
  Run: `docker compose exec api python -m pytest -q --ignore=tests/evals`
- `tests/evals/` — real-model conversation checks: `docker compose exec api python -m tests.evals.run_turn_evals`
- `tests/personas/` — six test travelers (bachelor group to Japan, Lisbon food couple, family beach, solo hiker,
  budget backpacker, luxury weekend) chat with the **running** app over HTTP and get pass/fail per rule: no invented
  profile facts, at most 8 questions before plans, drafts once destination/mood/party are known, picks fit the
  traveler. Personas and their scripted answers live in `personas.py`; the checks in `run.py`. Needs
  `ALLOW_DEV_LOGIN=true`; uses whatever model the server is configured with (local qwen3:8b takes a few minutes per
  persona). Exits non-zero on any failure. Not part of the pytest run.
  - All six, inside the container (after a rebuild so the image has the folder): `docker compose exec api python -m tests.personas.run`
  - From the host against any server (stdlib only): `cd travel-ai && python3 -m tests.personas.run --base-url http://localhost:8000`
  - One persona, longer per-turn timeout: `--persona bachelor --timeout 900` (`--persona` repeats)
