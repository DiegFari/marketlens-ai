# syntax=docker/dockerfile:1.7
# MarketLens AI — web frontend (React) served by Caddy, which also proxies the public
# API routes and handles HTTPS. Build from the repo root:
#   docker build -f docker/web.Dockerfile -t marketlens-web .

FROM node:22.11.0-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM caddy:2.8-alpine AS runtime
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
# Caddy keeps certificates in /data (a volume in docker-compose.prod.yml).
EXPOSE 80 443
