# Deploying Adieu

The public Adieu runs on the owner's Mac as a **separate production stack** beside the dev app, and reaches
the internet through **Tailscale Funnel** (free), which also provides HTTPS at
`https://<machine>.<tailnet>.ts.net`. No port is opened on the router.

|  | Dev app | Production |
|---|---|---|
| Folder | your working checkout | its own clone, `~/adieu-prod` |
| Compose project | `travel-ai` | `adieu-prod` |
| Env file | `travel-ai/.env` | `travel-ai/.env.prod` |
| Address | http://localhost:8080 (dev sign-in allowed) | https://&lt;DOMAIN&gt; (Funnel) and http://127.0.0.1:8090 |
| Database | its own volume | its own volume (`adieu-prod_postgres_data`) |

Only the production port (8090) goes through Funnel, never 8080. `docker-compose.prod.yml` forces `APP_ENV=production`
and dev sign-in off, and nginx adds the security headers and reads the visitor's address from the
`X-Forwarded-For` entry Funnel adds.

**The link is down whenever the Mac sleeps, is off, loses its connection or Tailscale is quit.**

## Redeploy

```bash
~/adieu-prod/travel-ai/deploy.sh
```

It pulls `main`, rebuilds, restarts what changed and prunes old images. Migrations run when the API starts.

## First-time setup

1. **Clone**: `git clone https://github.com/XclaimR/Adieu-2.0.git ~/adieu-prod`
2. **Tailscale**: install the standalone Mac app from https://tailscale.com/download and sign in to a free
   account. In the admin console (https://login.tailscale.com/admin):
   - Machines → this Mac → ⋯ → Edit machine name → `adieu` (the URL becomes `https://adieu.<tailnet>.ts.net`).
   - DNS → enable **MagicDNS** and **HTTPS Certificates**.
   - Access controls → make sure the policy has
     `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]`.
   - The tailnet name (`<tailnet>.ts.net`) is on the DNS page.
3. **Secrets**: create `~/adieu-prod/travel-ai/.env.prod` yourself (`chmod 600`); `.env.*` is gitignored:

   | Key | Value |
   |---|---|
   | `DOMAIN` | `adieu.<tailnet>.ts.net`, no `https://` (sets `CORS_ORIGINS`) |
   | `APP_ENV` | `production` |
   | `SESSION_SECRET` | `openssl rand -hex 32` (new, not your dev one) |
   | `POSTGRES_PASSWORD` | `openssl rand -hex 24`; must be set before the first start |
   | `GOOGLE_OAUTH_CLIENT_ID` | the Google OAuth client ID |
   | `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
   | `LLM_MODEL` | `gemini-3.5-flash-lite` |
   | `LLM_API_KEY` | the Gemini API key |
   | `LLM_REASONING_EFFORT` | empty |
   | `ALLOW_DEV_LOGIN` | `false` (forced off anyway) |
   | `TURNS_PER_DAY`, `TURNS_PER_MINUTE` | optional; defaults 40 and 5 per traveler |

   Leave any Google Places key out.
4. **Google Sign-In**: Google Cloud console → APIs & Services → Credentials → the OAuth client →
   Authorized JavaScript origins → add `https://<DOMAIN>`.
5. **Start**: `~/adieu-prod/travel-ai/deploy.sh`, check http://127.0.0.1:8090 works, then publish it:
   `tailscale funnel --bg 8090` (on the Mac app the CLI is
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale`). `tailscale funnel status` shows the URL;
   `tailscale funnel --https=443 off` takes it down.
6. **Keep it running**:
   - Docker Desktop → Settings → General → *Start Docker Desktop when you sign in*. Every container has
     `restart: unless-stopped`, so the stack comes back with Docker.
   - Tailscale → Settings → *Launch at login*. The `--bg` Funnel setting survives restarts.
   - System Settings → Battery (or Energy) → *Prevent automatic sleeping when the display is off*
     (on power adapter). For a one-off, `caffeinate -s` in a terminal keeps it awake until you stop it.
7. **Backups**: `crontab -e` and add

   ```
   15 3 * * * $HOME/adieu-prod/travel-ai/backup.sh >> $HOME/backups/adieu/backup.log 2>&1
   ```

   A gzipped `pg_dump` lands in `~/backups/adieu` nightly (if the Mac is awake at 03:15); the last 7 days
   are kept. Restore, from `~/adieu-prod/travel-ai`:

   ```bash
   gunzip -c ~/backups/adieu/adieu-<date>.sql.gz | docker compose -p adieu-prod --env-file .env.prod \
     -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U postgres travel_ai
   ```

## Checks

```bash
curl -sI https://<DOMAIN> | grep -iE 'strict-transport|x-content-type|x-frame'
curl -s https://<DOMAIN>/api/health
docker compose -p adieu-prod logs -f --tail=50 api
tailscale funnel status
```

## Stop

```bash
tailscale funnel --https=443 off
docker compose -p adieu-prod down
```

The database volume stays; `deploy.sh` and `tailscale funnel --bg 8090` bring it all back.
