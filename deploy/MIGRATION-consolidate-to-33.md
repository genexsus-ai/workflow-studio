# Migration runbook — consolidate onto 172.20.151.33

**Goal:** make `.33` (OptiPlex 5060, 6 cores / 30 GB) the single k3s control-plane running
the Studio, and re-join `.27` (HP EliteDesk, 8 cores / 3.7 GB) and `.28` (ThinkCentre) as
worker agents. Postgres on `.24` is unaffected.

**Current topology (confirmed):** two *separate* single-node clusters —
- Cluster A: `.27` (`k3s server`) + `.28` (`k3s agent`) — runs the Studio.
- Cluster B: `.33` (`k3s server`, standalone) — runs its own apps.

**Hostnames** (k3s node names, lower-cased):
- `.27` → `irsal2009-hp-elitedesk-800-g3-dm-35w`  ← current nodeSelector target
- `.28` → `irsal2009-thinkcentre-m75n`
- `.33` → `irsal2009-optiplex-5060`  ← new nodeSelector target

Every step needs sudo on the box named. Do the phases in order: **bring up on .33 and verify
before tearing down .27.**

---

## Phase 0 — Pre-flight (do NOT skip)

```bash
# 0a. Fresh on-demand DB backup on the DB host (.24). Restore point = minutes old.
ssh irsal2009@172.20.151.24 '~/db-backups/backup.sh'

# 0b. Inspect what's actually in the Studio PVC on .27 (real data is in Postgres;
#     this should be only the binary file-store / agent-memory / transient oauth state).
ssh irsal2009@172.20.151.27 \
  'sudo ls -laR /var/lib/rancher/k3s/storage/*genxflowstudio-data* 2>/dev/null | head -80'
# Note the exact source dir name (pvc-<uuid>_genxflowstudio_genxflowstudio-data) — needed in Phase A.

# 0c. Snapshot the two secrets from .27 so you can recreate them verbatim on .33.
ssh irsal2009@172.20.151.27 \
  'sudo k3s kubectl -n genxflowstudio get secret backend-env cloudflared-token -o yaml' \
  > ~/studio-secrets-backup.yaml   # keep this file OFF the repo; delete after migration
```

## Phase A — Stand up the Studio on .33's cluster (no downtime yet)

```bash
# A1. Join token from the target control-plane (.33) — needed in Phase C.
ssh irsal2009@172.20.151.33 'sudo cat /var/lib/rancher/k3s/server/node-token'
#   → save as <NODE_TOKEN>

# A2. Build the backend image locally and import into .33's containerd.
#     (run from repo root: /Users/irsalimran/Desktop/GenXAI-framework)
cd applications/workflow_studio
docker build -f deploy/Dockerfile -t genxflowstudio-backend:latest .
docker save genxflowstudio-backend:latest \
  | ssh irsal2009@172.20.151.33 'sudo k3s ctr images import -'

# A3. Recreate the namespace + secrets on .33 (strip resourceVersion/uid/creationTimestamp
#     from the Phase-0c yaml, or recreate imperatively — same values as .27):
ssh irsal2009@172.20.151.33 'sudo k3s kubectl create namespace genxflowstudio'
#   then apply the cleaned backend-env + cloudflared-token secrets into that namespace.

# A4. Apply the manifest on .33 with TWO edits (see below), cloudflared held at 0 replicas:
#       - nodeSelector hostname  → irsal2009-optiplex-5060
#       - cloudflared replicas   → 0   (so it doesn't fight .27 for the tunnel yet)
#     scp the edited genxflowstudio.yaml to .33, then:
ssh irsal2009@172.20.151.33 'sudo k3s kubectl apply -f genxflowstudio.yaml'

# A5. WAIT for the PVC to bind (a pod must schedule first), then copy the file-store in.
#     Scale backend to 0, copy data into the freshly-created storage dir, scale back to 1:
ssh irsal2009@172.20.151.33 'sudo k3s kubectl -n genxflowstudio scale deploy/genxflowstudio-backend --replicas=0'
#     find the NEW dir on .33:
ssh irsal2009@172.20.151.33 'sudo ls -d /var/lib/rancher/k3s/storage/*genxflowstudio-data*'
#     stream .27's PVC contents → .33's PVC dir (adjust the two paths from 0b / above):
ssh irsal2009@172.20.151.27 'sudo tar -C <SRC_DIR_ON_27> -czf - .' \
  | ssh irsal2009@172.20.151.33 'sudo tar -C <DST_DIR_ON_33> -xzf -'
ssh irsal2009@172.20.151.33 'sudo k3s kubectl -n genxflowstudio scale deploy/genxflowstudio-backend --replicas=1'

# A6. Verify the backend is healthy on .33 WITHOUT touching the live tunnel:
ssh irsal2009@172.20.151.33 \
  'sudo k3s kubectl -n genxflowstudio port-forward deploy/genxflowstudio-backend 8000:8000 & \
   sleep 4; curl -s localhost:8000/health; kill %1'
#   → expect {"status":"ok"} (or the app's health payload). Do NOT proceed until this is green.
```

## Phase B — Cut over the tunnel (brief blip only)

```bash
# B1. Stop the old connector, start the new one. Same tunnel token → Cloudflare re-routes
#     api.genxflowstudio.com to .33's backend. No DNS change.
ssh irsal2009@172.20.151.27 'sudo k3s kubectl -n genxflowstudio scale deploy/cloudflared --replicas=0'
ssh irsal2009@172.20.151.33 'sudo k3s kubectl -n genxflowstudio scale deploy/cloudflared --replicas=1'

# B2. Verify from the public edge:
curl -s https://api.genxflowstudio.com/health
#   Roll back instantly if needed: reverse the two scale commands.
```

## Phase C — Re-join .27 and .28 as agents of .33  (only after B is green)

```bash
# C1. .28 (currently an agent of .27) → re-point to .33:
ssh irsal2009@172.20.151.28 'sudo /usr/local/bin/k3s-agent-uninstall.sh'
ssh irsal2009@172.20.151.28 \
  'curl -sfL https://get.k3s.io | K3S_URL=https://172.20.151.33:6443 K3S_TOKEN=<NODE_TOKEN> sh -'

# C2. .27 (server) → demote to agent of .33. This wipes cluster A (Studio already moved).
ssh irsal2009@172.20.151.27 'sudo /usr/local/bin/k3s-uninstall.sh'
ssh irsal2009@172.20.151.27 \
  'curl -sfL https://get.k3s.io | K3S_URL=https://172.20.151.33:6443 K3S_TOKEN=<NODE_TOKEN> sh -'

# C3. Verify final cluster:
ssh irsal2009@172.20.151.33 'sudo k3s kubectl get nodes -o wide'
#   → .33 = control-plane,master ; .27 and .28 = <none> (agents), all Ready.
```

## Post-migration
- Commit the edited `genxflowstudio.yaml` (new nodeSelector hostname = `irsal2009-optiplex-5060`).
- Delete `~/studio-secrets-backup.yaml` from wherever you saved it.
- The Studio backend now has 30 GB to breathe in instead of ~1 GB free.

## Rollback
- Before Phase C: re-scale .33 cloudflared→0 and .27 cloudflared→1 (Phase B reverse).
  Cluster A is untouched, so the old Studio is back in seconds.
- After Phase C: cluster A no longer exists — recovery is redeploy from repo + the .24 DB
  backup. This is why Phase 0a (fresh backup) and Phase A6 (verify healthy) are mandatory.
```
