# Deploying on a small Linux server

Runs the whole stack (API + UI + HTTPS) on one Ubuntu 24.04 server with 1 core and
2 GB RAM, for example a STRATO VPS Linux S. The language model runs at Groq, so the
server itself does very little work.

```
Internet ──https──▶ Caddy (80/443) ──▶ UI (Streamlit) ──▶ API (FastAPI) ──▶ Groq, FMP, NewsAPI
                    only public part   private network    private network
```

## 1. Pick the address

Caddy needs a host name to get a free HTTPS certificate.

* **Own domain:** create a DNS `A` record, e.g. `marketlens.example.com → <server IPv4>`,
  and use `SITE_ADDRESS=marketlens.example.com`.
* **No domain:** use the free [sslip.io](https://sslip.io) name that maps to your IP
  with dashes: for `203.0.113.10` that is `SITE_ADDRESS=203-0-113-10.sslip.io`.

## 2. Open the provider firewall

In the hosting panel's firewall (STRATO: *Firewall*), allow inbound **22, 80 and 443
(TCP)**. Ports 8000 and 8501 must stay closed; they are not used publicly.

## 3. Prepare the server (once)

```bash
ssh root@<server-ip>
git clone https://github.com/DiegFari/marketlens-ai.git /opt/marketlens
cd /opt/marketlens
bash deploy/setup-server.sh        # updates, Docker, firewall, swap
```

If the server came with Plesk or another web server, check nothing else uses ports 80
and 443: `ss -tlnp | grep -E ':(80|443) '` should print nothing.

## 4. Configure

```bash
cp .env.example .env
nano .env
```

Fill in `FMP_API_KEY`, `NEWSAPI_API_KEY`, `GROQ_API_KEY`, and add:

```bash
SITE_ADDRESS=203-0-113-10.sslip.io      # or your domain
UI_ACCESS_PASSWORD=choose-a-long-password  # visitors type this to get in
```

Then `chmod 600 .env`.

## 5. Start

```bash
docker compose -f docker-compose.prod.yml up -d --build   # first build: ~5-10 min
docker compose -f docker-compose.prod.yml ps               # all "running", api "healthy"
```

Open `https://<SITE_ADDRESS>`, enter the password, and ask for a briefing.
Everything restarts automatically after a crash or a server reboot.

## Day to day

| Task | Command (in `/opt/marketlens`) |
|---|---|
| Logs | `docker compose -f docker-compose.prod.yml logs -f --tail 100` |
| Update to latest code | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| Change keys / password | edit `.env`, then `docker compose -f docker-compose.prod.yml up -d` |
| Stop | `docker compose -f docker-compose.prod.yml down` |
| Free disk from old images | `docker image prune -f` |

## Notes

* **Quotas are yours.** Every visitor's briefing uses your Groq, FMP and NewsAPI free
  quotas (a briefing is 4-6 model calls). Keep the password on and share it selectively.
* **NewsAPI's free plan** is licensed for development; a private, password-protected
  demo fits that, a fully public site does not.
* **Security:** prefer SSH keys over the root password (`ssh-copy-id root@<server-ip>`,
  then set `PasswordAuthentication no` in `/etc/ssh/sshd_config`). Security updates
  install automatically via `unattended-upgrades`.
