#!/bin/bash
# Stage A smoke + telemetry capture on the AWS substrate host.
# Deploys hash-polarization, captures shapes, tears down.

set -euxo pipefail

REPO=/opt/harnessit/containerlab-adapter
OUT=$REPO/scout-outputs
TOPO=$REPO/src/containerlab_adapter/topologies/hash_polarization.clab.yaml

cd $REPO
mkdir -p $OUT
chown ubuntu:ubuntu $OUT

# Clean any prior deploy
containerlab destroy -t $TOPO --cleanup 2>/dev/null || true

# Deploy
echo "=== deploy ==="
containerlab deploy -t $TOPO 2>&1 | tail -3

echo "=== state right after deploy ==="
docker ps --filter name=clab-hash-polarization --format 'table {{.Names}}\t{{.Status}}'

# Wait for SONiC services to boot
echo "=== waiting 60s for SONiC bootstrap ==="
sleep 60

echo "=== state after 60s ==="
docker ps --filter name=clab-hash-polarization --format 'table {{.Names}}\t{{.Status}}'

# Capture inspect.json
containerlab inspect -t $TOPO --format json 2>/dev/null > $OUT/inspect.json
echo "=== inspect.json bytes: $(wc -c < $OUT/inspect.json) ==="

# Capture per-SONiC-node telemetry
for node in spine1 leaf1 leaf2; do
  CN=clab-hash-polarization-$node
  echo "=== capturing $node ==="
  docker exec $CN supervisorctl status > $OUT/$node-supervisorctl.txt 2>&1 || echo "supervisorctl failed"
  for cmd in "show interfaces status" "show interfaces counters" "show queue counters" "show pfc counters" "show priority-group watermark headroom" "show ip bgp summary"; do
    fn=$(echo "$cmd" | tr ' ' '_')
    docker exec $CN bash -lc "$cmd" > $OUT/$node-$fn.txt 2>&1 || true
  done
  docker exec $CN ip link show > $OUT/$node-ip_link.txt 2>&1 || true
done

echo "=== capture summary ==="
ls -la $OUT/
chown -R ubuntu:ubuntu $OUT

echo "=== quick sanity: leaf1 supervisorctl head ==="
head -8 $OUT/leaf1-supervisorctl.txt

echo "=== leaf1 show interfaces status head ==="
head -10 $OUT/leaf1-show_interfaces_status.txt

echo "=== state after capture ==="
docker ps --filter name=clab-hash-polarization --format 'table {{.Names}}\t{{.Status}}'

# Teardown
echo "=== teardown ==="
containerlab destroy -t $TOPO --cleanup 2>&1 | tail -3

echo "=== SMOKE COMPLETE ==="
