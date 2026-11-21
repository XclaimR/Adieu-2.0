# travel-ai-ui

A very basic chat UI for the Adieu travel-ai backend. No build step, no npm,
no dependencies: one HTML file plus a tiny Python server.

## Run it

Terminal 1, backend:

    cd ~/Projects/Adieu/travel-ai
    python3 -m venv .venv && source .venv/bin/activate   # first time only
    pip install -r requirements.txt                      # first time only
    uvicorn app.main:app --reload --port 8000

Terminal 2, UI:

    cd ~/Projects/Adieu/travel-ai-ui
    python3 serve.py

Open http://localhost:5173

## How it works

- `index.html` posts `{conversation_id, message}` to `/api/chat` and renders
  `assistant_message`, the extracted `profile` (required fields shown in orange
  until filled) and the `itinerary`.
- `serve.py` serves the page and forwards `/api/*` to the backend
  (`BACKEND_URL`, default http://localhost:8000), so the backend needs no CORS setup.
- "New trip" clears the conversation id. Conversations live in the backend's
  memory, so restarting uvicorn forgets them.
- The raw JSON of the last response is shown at the bottom of the sidebar for debugging.

Env vars: `BACKEND_URL`, `PORT`.
