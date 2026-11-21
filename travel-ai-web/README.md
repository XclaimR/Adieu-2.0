# Adieu web

React + Vite frontend for the `travel-ai` API.

## Run everything

From `../travel-ai`:

    ollama pull qwen3:8b            # once; Ollama must be running on the host
    docker compose up -d --build

- Web app: http://localhost:8080
- API docs: http://localhost:8000/docs

The web container (nginx) serves the built app and forwards `/api/*` to the API container.

## Configuration (`travel-ai/.env`)

| Variable | What it does |
|---|---|
| `GOOGLE_PLACES_API_KEY` | Real place suggestions and photos. Without it, chat works but no pick cards appear. |
| `GOOGLE_OAUTH_CLIENT_ID` | Shows the "Sign in with Google" button. Add `http://localhost:8080` as an authorized JavaScript origin. |
| `ALLOW_DEV_LOGIN` | Email-only sign-in for local development. Off by default; set `ALLOW_DEV_LOGIN=true` in `.env` for local development. |
| `SESSION_SECRET` | Signs login sessions. |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Any OpenAI-compatible provider. Defaults to local Ollama with `qwen3:8b`. |
| `LLM_REASONING_EFFORT` | `none` by default (much faster on qwen3). Set it empty for providers that reject the parameter. |

Switching to Gemini, for example:

    LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
    LLM_MODEL=gemini-3.1-flash-lite     # the model the old code used
    LLM_API_KEY=<your Gemini key>

After switching models, rerun the conversation evals:

    docker compose exec api python -m tests.evals.run_turn_evals

## Developing without Docker

    npm install
    BACKEND_URL=http://localhost:8000 npm run dev
