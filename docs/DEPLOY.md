# Deploying Adieu

Adieu runs on one Oracle Cloud Always Free VM (Ampere A1, Ubuntu, Arm) with the same Docker Compose as
locally, plus `travel-ai/docker-compose.prod.yml`, which hides Postgres, the API and nginx and puts
[Caddy](https://caddyserver.com) in front for HTTPS (Let's Encrypt, renewed automatically). Only ports
80 and 443 are open.

## Redeploy

```bash
ssh ubuntu@<server> ~/Adieu-2.0/travel-ai/deploy.sh
```

`deploy.sh` pulls `main`, rebuilds, restarts what changed and prunes old images. Migrations run when the
API starts.

## First-time setup

1. **VM**: Oracle console → Compute → Instances → Create: image Canonical Ubuntu (latest LTS), shape
   `VM.Standard.A1.Flex` (2 OCPU, 12 GB), your SSH public key, a public IPv4 address.
2. **Ports**: the subnet's security list gets ingress rules for TCP 80 and 443 from `0.0.0.0/0`.
3. **Hostname**: an A record for your domain pointing at the VM's public IP, or, with no domain,
   `<ip-with-dashes>.sslip.io` (e.g. `203-0-113-7.sslip.io`).
4. **Server** (as `ubuntu`):

   ```bash
   sudo apt-get update && sudo apt-get -y upgrade
   curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker ubuntu
   # Oracle's Ubuntu images reject everything but SSH in iptables.
   sudo iptables -I INPUT 5 -p tcp -m multiport --dports 80,443 -j ACCEPT
   sudo iptables -I INPUT 5 -p udp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   sudo apt-get -y install unattended-upgrades && sudo dpkg-reconfigure -f noninteractive unattended-upgrades
   sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   git clone https://github.com/XclaimR/Adieu-2.0.git ~/Adieu-2.0
   ```

5. **Secrets**: create `~/Adieu-2.0/travel-ai/.env` on the server yourself (`chmod 600`), never in git:

   | Key | Value |
   |---|---|
   | `DOMAIN` | the hostname from step 3, no `https://` |
   | `APP_ENV` | `production` |
   | `SESSION_SECRET` | `openssl rand -hex 32` (new, not your local one) |
   | `POSTGRES_PASSWORD` | `openssl rand -hex 24` (read only when the database is first created) |
   | `GOOGLE_OAUTH_CLIENT_ID` | the Google OAuth client ID |
   | `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
   | `LLM_MODEL` | `gemini-3.5-flash-lite` |
   | `LLM_API_KEY` | the Gemini API key |
   | `LLM_REASONING_EFFORT` | empty |
   | `ALLOW_DEV_LOGIN` | `false` (the override forces it off anyway) |

   Leave any Google Places key out.

6. **Google Sign-In**: Google Cloud console → APIs & Services → Credentials → the OAuth client →
   Authorized JavaScript origins → add `https://<DOMAIN>`.
7. **Start**: `~/Adieu-2.0/travel-ai/deploy.sh`, then open `https://<DOMAIN>`.
8. **Backups**: `crontab -e` and add

   ```
   15 3 * * * $HOME/Adieu-2.0/travel-ai/backup.sh >> $HOME/backups/backup.log 2>&1
   ```

   A gzipped `pg_dump` lands in `~/backups` nightly; the last 7 days are kept. They live on the same VM,
   so copy one off now and then. Restore:
   `gunzip -c ~/backups/adieu-<date>.sql.gz | docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T postgres psql -U postgres travel_ai`

## Checks

```bash
curl -sI https://<DOMAIN> | grep -iE 'strict-transport|x-content-type|x-frame'
curl -s https://<DOMAIN>/api/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f --tail=50 api
```
