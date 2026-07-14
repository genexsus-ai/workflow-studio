# Deploying the Workflow Studio backend to k3s

Target: the k3s cluster at 172.20.151.27 (control-plane) / .28 (worker),
public via Cloudflare Tunnel at https://api.genxflowstudio.com.
Postgres lives on 172.20.151.24 (`genxflowstudio` database); both node IPs
need `pg_hba.conf` entries there.

## Build & import the image

The build context needs this repo's `backend/` plus the `genxai/` package
directory from the genxai-framework repo:

```bash
# staging dir containing: Dockerfile (from deploy/), backend/, genxai/
docker build -t genxflowstudio-backend:latest .
docker save genxflowstudio-backend:latest | sudo k3s ctr images import -
```

The deployment pins to the control-plane node (local-path PVC is
node-local), so importing on that node is sufficient.

## Deploy

```bash
sudo k3s kubectl apply -f k8s/genxflowstudio.yaml
```

Secrets are created once, imperatively — see the header comment in
`k8s/genxflowstudio.yaml` for both `backend-env` (app configuration,
including `STUDIO_API_TOKEN`, which public clients must send as the
`X-Studio-Token` header) and `cloudflared-token`.

## Public hostname (Cloudflare Tunnel)

In Cloudflare Zero Trust → Networks → Tunnels: create a tunnel (e.g.
`genxflowstudio`), add public hostname `api.genxflowstudio.com` →
`http://genxflowstudio-backend.genxflowstudio.svc.cluster.local:8000`,
and put its token in the `cloudflared-token` secret. Cloudflare
terminates TLS and routes into the cluster; no ports are opened on the
router.

## Runtime data

Workflows, runs, datasets and the app tables live in Postgres. The PVC
mounted at `/app/backend/data` holds the file store (`files/`),
`credentials.json` and `mcp_servers.json`; seed it from a dev machine
with `kubectl cp` if migrating.
