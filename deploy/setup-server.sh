#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu 24.04 server for MarketLens AI.
# Run as root:  bash deploy/setup-server.sh
#
# Installs security updates, Docker, a firewall (SSH + web only), automatic security
# updates, and a 2 GB swap file so image builds don't run out of memory on 2 GB RAM.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root." >&2
  exit 1
fi

echo "==> Installing security updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get -y install ca-certificates curl git ufw unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Installing Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> Adding 2 GB swap (if none)"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Firewall: allow SSH, HTTP, HTTPS only"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo
echo "Done. PHP check (only matters if you use PHP/Plesk sites):"
if command -v php >/dev/null; then php -v | head -1; else echo "  PHP is not installed - nothing to update."; fi
echo
echo "Next: clone the project, create .env, and run"
echo "  docker compose -f docker-compose.prod.yml up -d --build"
