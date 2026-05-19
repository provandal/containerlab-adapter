#!/bin/bash
# Idempotent host setup for the HarnessIT substrate EC2 instance.
# Run remotely via `aws ssm send-command` (as root).
# Stages: apt deps -> Docker -> containerlab -> SONiC image -> repo + venv -> verify.

set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive

# 1. Base apt packages
apt-get update -qq
apt-get install -y --no-install-recommends \
  git python3 python3-venv python3-pip iproute2 net-tools curl jq ca-certificates

# 2. Docker Engine (native, not Docker Desktop)
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker ubuntu 2>/dev/null || true
systemctl enable --now docker

# 3. containerlab
if ! command -v containerlab >/dev/null 2>&1; then
  bash -c "$(curl -sL https://get.containerlab.dev)"
fi

# 4. Pull SONiC image (community netreplica build)
if ! docker image inspect netreplica/docker-sonic-vs:latest >/dev/null 2>&1; then
  docker pull netreplica/docker-sonic-vs:latest
fi

# 5. Clone repo into /opt/harnessit/containerlab-adapter (ubuntu owns it)
mkdir -p /opt/harnessit
chown ubuntu:ubuntu /opt/harnessit
if [ ! -d /opt/harnessit/containerlab-adapter ]; then
  sudo -u ubuntu git clone https://github.com/provandal/containerlab-adapter.git /opt/harnessit/containerlab-adapter
else
  sudo -u ubuntu git -C /opt/harnessit/containerlab-adapter pull --ff-only
fi

# 6. Python venv + editable install
cd /opt/harnessit/containerlab-adapter
if [ ! -d .venv ]; then
  sudo -u ubuntu python3 -m venv .venv
fi
sudo -u ubuntu .venv/bin/pip install --quiet --upgrade pip
sudo -u ubuntu .venv/bin/pip install --quiet -e ".[dev]"

# 7. Verify
echo "=== versions ==="
docker --version
containerlab version | head -3
.venv/bin/python --version
echo "=== docker images ==="
docker images | head -5
echo "=== pytest (hermetic) ==="
sudo -u ubuntu .venv/bin/pytest -q 2>&1 | tail -5
echo "=== check_setup.py ==="
sudo -u ubuntu .venv/bin/python scripts/check_setup.py
echo "=== disk ==="
df -h / | head -2
echo "=== mem ==="
free -h | head -3
echo "=== SETUP COMPLETE ==="
