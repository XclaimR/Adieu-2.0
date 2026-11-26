# Adieu web

React + Vite frontend for the `travel-ai` API.

## Run everything

From `../travel-ai`:

    ollama pull qwen3:8b            # once; Ollama must be running on the host
    docker compose up -d --build

- Web app: http://localhost:8080
- API docs: http://localhost:8000/docs

The web container (nginx) serves the built app and forwards `/api/*` to the API container.

Places come from open data and need no key: Wikidata, Wikivoyage and Wikimedia Commons (through their APIs, at
most one request a second) and Overture Maps (read from its public files with DuckDB). Each destination is loaded
once (15-30 s) and kept in Postgres; credits are under "Data credits" in the app.

## Configuration (`travel-ai/.env`)

| Variable | What it does |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | Shows the "Sign in with Google" button. Add `http://localhost:8080` as an authorized JavaScript origin. |
| `ALLOW_DEV_LOGIN` | Email-only sign-in for local development. Off by default; set `ALLOW_DEV_LOGIN=true` in `.env` for local development. |
| `APP_ENV` | `production` (default) refuses to start with a missing, default or short (<32) `SESSION_SECRET`, the default database password or `ALLOW_DEV_LOGIN` on. Use `development` on your own machine. |
| `SESSION_SECRET` | Signs login sessions. At least 32 random characters in production. |
| `POSTGRES_PASSWORD` | Database password (letters and digits). Read only when the Postgres volume is first created. |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Any OpenAI-compatible provider. Without them the app uses local Ollama with `qwen3:8b`. |
| `LLM_REASONING_EFFORT` | `none` by default (much faster on qwen3). Set it empty for providers that reject the parameter. |
| `TURNS_PER_MINUTE`, `TURNS_PER_DAY` | Per traveler: model-using requests (chat turns, cheaper-stay and swap lookups) per minute (default 5) and per rolling 24 hours (default 40). Past them the traveler gets a 429 with a message. Raise `TURNS_PER_DAY` locally for persona/agent runs, which all use one dev account. |

**Recommended: Gemini's free tier** (a turn with 3 drafts takes ~12s instead of minutes on qwen3:8b). Get a key at https://aistudio.google.com/apikey:

    LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
    LLM_MODEL=gemini-3.5-flash-lite
    LLM_API_KEY=<your Gemini key>
    LLM_REASONING_EFFORT=           # this model rejects the parameter

Free-tier caveats:
- **Privacy:** Google may use free-tier prompts to improve its products. Don't send anything private (screenshots and Instagram data stay on the local model).
- **Rate limit:** 15 requests per minute per model. A chat turn with drafts uses ~4–5, so a few fast turns can hit it. The app waits the delay Gemini asks for (up to 30s) and tries again; a rate-limited call makes at most 4 attempts (the OpenAI library's own retry, then one more round after the wait) before giving up.
- To go back to Ollama, remove the four `LLM_` lines and restart the API.

After switching models, rerun the conversation evals:

    docker compose run --rm --build tests python -m tests.evals.run_turn_evals

## Developing without Docker

    npm install
    BACKEND_URL=http://localhost:8000 npm run dev
