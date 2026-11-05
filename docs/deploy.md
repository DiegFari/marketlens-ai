# Deploying on a small Linux server

Runs the whole stack (React app + API + HTTPS) on one Ubuntu 24.04 server with 1 core and
2 GB RAM, for example a STRATO VPS Linux S. The language model runs at Groq, so the
server itself does very little work.

```
Internet ──https──▶ web: Caddy (80/443) ──▶ api: FastAPI ──▶ Groq, FMP, NewsAPI
                    React app + public       private network,
                    API routes only          no published ports
```

The site is open to anyone; free-tier quotas are protected by the API instead of a
password: repeat requests are served from a 6-hour cache, new briefings are capped per
day and per visitor, and one briefing runs at a time (others queue briefly). Tune with
the `DEMO_*` settings in `.env.example`.

## 1. Pick the address

Caddy needs a host name to get a free HTTPS certificate.

* **Own domain:** create a DNS `A` record, e.g. `marketlens.example.com → <server IPv4>`,
  and use `SITE_ADDRESS=marketlens.example.com`.
* **Free name:** [DuckDNS](https://www.duckdns.org) gives e.g. `marketlens.duckdns.org`
  pointing at your IP.
* **No setup at all:** [sslip.io](https://sslip.io) maps a name to your IP with dashes:
  for `203.0.113.10` that is `SITE_ADDRESS=203-0-113-10.sslip.io`.

## 2. Open the provider firewall

In the hosting panel's firewall (STRATO: *Firewall*), allow inbound **22, 80 and 443
(TCP)**. Port 8000 must stay closed; the API is not used publicly.

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
```

Then `chmod 600 .env`.

## 5. Start

```bash
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans   # first build: ~5-10 min
docker compose -f docker-compose.prod.yml ps                              # api "healthy", web "running"
```

Open `https://<SITE_ADDRESS>` and ask for a briefing. Everything restarts automatically
after a crash or a server reboot.

## Day to day

| Task | Command (in `/opt/marketlens`) |
|---|---|
| Logs | `docker compose -f docker-compose.prod.yml logs -f --tail 100` |
| Update to latest code | `git pull && docker compose -f docker-compose.prod.yml up -d --build --remove-orphans` |
| Change keys or limits | edit `.env`, then `docker compose -f docker-compose.prod.yml up -d` |
| Stop | `docker compose -f docker-compose.prod.yml down` |
| Free disk from old images | `docker image prune -f` |

`--remove-orphans` removes containers from earlier versions of the stack (e.g. the old
Streamlit `ui` and `caddy` services), which would otherwise keep ports 80/443 busy.

## Notes

* **Quotas are yours.** Every new briefing uses your Groq, FMP and NewsAPI free quotas
  (4-6 model calls each). The demo limits keep that bounded; lower `DEMO_DAILY_BRIEFINGS`
  if you hit provider limits.
* **NewsAPI's free plan** is licensed for development and delays articles by about a
  day; a personal portfolio demo with low traffic is the intended scale.
* **Security:** prefer SSH keys over the root password (`ssh-copy-id root@<server-ip>`,
  then set `PasswordAuthentication no` in `/etc/ssh/sshd_config`). Security updates
  install automatically via `unattended-upgrades`.
