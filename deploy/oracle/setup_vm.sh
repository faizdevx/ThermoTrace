#!/usr/bin/env bash
# One-time preparation of an Oracle Cloud Always Free VM (Ubuntu 24.04, Ampere A1 / aarch64) for ThermoTrace.
# Run as the default "ubuntu" user:  bash deploy/oracle/setup_vm.sh
# Idempotent: safe to run again.
set -euo pipefail

echo "== packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl python3 iptables-persistent unattended-upgrades

echo "== docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

echo "== firewall: Oracle Ubuntu images reject everything except SSH in iptables, open 80 and 443"
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null; then
    # insert before the image's final REJECT rule (position 6 on stock images; 1 is also correct, just less tidy)
    sudo iptables -I INPUT 6 -p tcp -m state --state NEW --dport "$port" -j ACCEPT 2>/dev/null \
      || sudo iptables -I INPUT 1 -p tcp -m state --state NEW --dport "$port" -j ACCEPT
  fi
done
sudo netfilter-persistent save

echo "== swap (2 GB, guards the build against memory spikes)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== automatic security updates"
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Done. Log out and back in once (docker group), then follow docs/DEPLOY_ORACLE_FREE.md step 5."
