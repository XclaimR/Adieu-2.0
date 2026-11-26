# Deploying Adieu

The public Adieu runs on the owner's Mac as a **separate production stack** beside the dev app, and reaches
the internet through a **Cloudflare Tunnel**, which also provides HTTPS. No port is opened on the router.

|  | Dev app | Production |
|---|---|---|
| Folder | your working checkout | its own clone, `~/adieu-prod` |
| Compose project | `travel-ai` | `adieu-prod` |
| Env file | `travel-ai/.env` | `travel-ai/.env.prod` |
| Address | http://localhost:8080 (dev sign-in allowed) | https://&lt;DOMAIN&gt; and http://127.0.0.1:8090 |
| Database | its own volume | its own volume (`adieu-prod_postgres_data`) |

Only the production stack goes through the tunnel. `docker-compose.prod.yml` forces `APP_ENV=production`
and dev sign-in off, and nginx adds the security headers and reads the visitor's address from
`CF-Connecting-IP`.

**The link is down whenever the Mac sleeps, is off or loses its connection.**

## Redeploy

```bash
~/adieu-prod/travel-ai/deploy.sh
```

It pulls `main`, rebuilds, restarts what changed and prunes old images. Migrations run when the API starts.

## First-time setup

1. **Clone**: `git clone https://github.com/XclaimR/Adieu-2.0.git ~/adieu-prod`
2. **Tunnel**: in the Cloudflare dashboard (Zero Trust → Networks → Tunnels → Create a tunnel, type
   Cloudflared), name it `adieu`, copy its token, and add a public hostname on your domain with service
   `http://web:80`. The domain must be on Cloudflare.
3. **Secrets**: create `~/adieu-prod/travel-ai/.env.prod` yourself (`chmod 600`); `.env.*` is gitignored:

   | Key | Value |
   |---|---|
   | `DOMAIN` | the public hostname, no `https://` |
   | `TUNNEL_TOKEN` | the tunnel token from step 2 |
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
5. **Start**: `~/adieu-prod/travel-ai/deploy.sh`, then open `https://<DOMAIN>`.
6. **Keep it running**:
   - Docker Desktop → Settings → General → *Start Docker Desktop when you sign in*. Every container has
     `restart: unless-stopped`, so the stack and tunnel come back with Docker.
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
docker compose -p adieu-prod logs -f --tail=50 api tunnel
```

## Stop

```bash
docker compose -p adieu-prod down
```

The database volume stays; `deploy.sh` brings it all back.
