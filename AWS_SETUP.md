# AWS setup — containerlab-adapter

Provisioning steps for running the substrate work on a Linux EC2 instance. Use this instead of the WSL2-side setup in the README when local resources are constrained or when you want a dedicated dev environment.

## Why AWS

The local WSL2 + Docker Desktop path hit a cumulative resource pressure ceiling — SONiC containers' working set (~500-700 MB each) plus the rest of the laptop's workload triggered cgroup-level pressure that killed SONiC containers simultaneously at ~10-min intervals. AWS gives us isolation + headroom without continued WSL2/Docker-Desktop debugging.

## Instance recommendation

- **Type**: `t3.xlarge` (4 vCPU, 16 GB RAM) — fits a 6 SONiC + 8 host topology comfortably with room for Python venv, image cache, scout outputs
- **AMI**: Ubuntu 22.04 LTS or 24.04 LTS
- **Volume**: 50+ GB gp3 root volume (~$5/mo) — SONiC images, repos, scout outputs
- **Network**: SSH (22) from your laptop IP; no other inbound needed
- **Cost target**: stop the instance when not in active use (~$0.166/hr running, ~$0.005/hr stopped for EBS)

If you already have an instance (e.g., a stopped RKE2 agent from a prior project), you can repurpose it — containerlab doesn't need Kubernetes, just bare Linux.

## Provisioning (fresh)

```bash
# From your AWS-CLI-configured laptop:
aws ec2 run-instances \
  --image-id <ubuntu-2404-ami-for-your-region> \
  --instance-type t3.xlarge \
  --key-name <your-keypair> \
  --security-group-ids <sg-with-ssh-ingress> \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=50,VolumeType=gp3}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=harnessit-substrate}]'
```

## Starting an existing stopped instance

```bash
aws ec2 start-instances --instance-ids <i-...>
aws ec2 wait instance-running --instance-ids <i-...>
aws ec2 describe-instances --instance-ids <i-...> \
  --query "Reservations[0].Instances[0].[PublicDnsName,PublicIpAddress]" --output text
```

If the instance doesn't have a public IP / DNS (private-subnet only), reach it via:
- A bastion host in the same VPC (`ssh -J ubuntu@bastion-host ubuntu@private-host`)
- AWS SSM Session Manager (no SSH at all — uses IAM): `aws ssm start-session --target i-...`

## One-time host setup (run after first SSH)

```bash
# 1. Update + base packages
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  git python3 python3-venv python3-pip iproute2 net-tools curl jq

# 2. Docker Engine (NOT Docker Desktop — native daemon)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker  # or log out + back in

# 3. containerlab
bash -c "$(curl -sL https://get.containerlab.dev)"
containerlab version

# 4. Pull SONiC image (community build for containerlab)
docker pull netreplica/docker-sonic-vs:latest
docker images | grep sonic
```

## Clone repo + install Python package

```bash
mkdir -p ~/harnessit
cd ~/harnessit
git clone https://github.com/provandal/containerlab-adapter.git
cd containerlab-adapter

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Sanity
pytest                # 21 hermetic tests should pass
python scripts/check_setup.py   # all 4 OK
```

## Run the smoke test

```bash
python scripts/smoke_topology.py
```

This deploys the 3 SONiC + 4 host placeholder topology, captures `containerlab inspect --format json` to `scout-outputs/`, and tears down.

## Capture full telemetry artifacts (between deploy and destroy)

```bash
# Deploy
containerlab deploy -t src/containerlab_adapter/topologies/hash_polarization.clab.yaml

# Wait for SONiC services to boot
sleep 60

# Capture
mkdir -p scout-outputs
containerlab inspect -t src/containerlab_adapter/topologies/hash_polarization.clab.yaml \
  --format json > scout-outputs/inspect.json

for node in leaf1 leaf2 spine1; do
  docker exec clab-hash-polarization-$node supervisorctl status \
    > scout-outputs/$node-supervisorctl.txt 2>&1
  for cmd in "show interfaces status" "show interfaces counters" \
             "show queue counters" "show pfc counters" \
             "show priority-group watermark headroom" \
             "show ip bgp summary"; do
    fn=$(echo "$cmd" | tr ' ' '_')
    docker exec clab-hash-polarization-$node bash -lc "$cmd" \
      > scout-outputs/$node-$fn.txt 2>&1
  done
done

# Teardown
containerlab destroy -t src/containerlab_adapter/topologies/hash_polarization.clab.yaml --cleanup
```

## Stopping the instance when done

```bash
aws ec2 stop-instances --instance-ids <i-...>
```

Stopped instances cost only the EBS volume (~$5/mo for 50 GB gp3) — start them back up next session.

## RackIT-coexistence discipline

If repurposing an instance from an unrelated project (e.g., a RackIT-environment EC2 instance):

- Work in a dedicated directory (`~/harnessit/` or similar), separate from any other project paths
- Don't touch other project's Docker containers, images, or volumes — `docker ps`, `docker images`, `docker volume ls` may show pre-existing state; leave it alone
- HarnessIT git commits only include the HarnessIT repo's own files via paths — never `git add -A` from a parent directory
- If using ECR or other shared registries, push HarnessIT artifacts to HarnessIT repositories only
