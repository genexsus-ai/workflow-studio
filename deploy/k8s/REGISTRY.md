# In-cluster image registry (spread apps across nodes)

A small `registry:2` runs on the control-plane (**.33**) so any node (.33 / .27 / .28)
can pull the same image. This removes the old constraint where every app had to pin to
one node because its image was only imported into that node's containerd.

- **Registry address:** `172.20.151.33:5000` (plain HTTP, trusted LAN).
- **Manifest:** [`registry.yaml`](registry.yaml) — namespace `registry`, pinned to .33,
  hostPort 5000, 20Gi `local-path` PVC at `/var/lib/registry`.
- **Node config:** `/etc/rancher/k3s/registries.yaml` on **all three** nodes maps
  `172.20.151.33:5000` → `http://172.20.151.33:5000`. (Applied; k3s restarted to load it.)
- **Docker push host:** **.27** has docker with `/etc/docker/daemon.json` =
  `{"insecure-registries":["172.20.151.33:5000"]}`. (.33 has no docker, only `k3s ctr`
  which pushes unreliably — use docker on .27.)

## Build → push → deploy (the new pattern)

```bash
# on .27 (has docker):
docker build -t 172.20.151.33:5000/myapp:latest .
docker push  172.20.151.33:5000/myapp:latest
```

Then in the Deployment, reference the registry image and DON'T pin/anchor it:

```yaml
spec:
  replicas: 3                       # scheduler spreads these across .33/.27/.28
  template:
    spec:
      # no nodeSelector  -> free placement
      containers:
        - name: app
          image: 172.20.151.33:5000/myapp:latest
          imagePullPolicy: Always   # or IfNotPresent + a version tag
```

## Requirements for an app to actually spread

1. **Image in the registry** (above) — not just imported to one node.
2. **No `nodeSelector`** pinning it to a single host.
3. **No node-local `local-path` PVC** for state. Keep state in Postgres (**.24**); if the
   app needs persistent *files*, that ties it to one node unless you move to shared storage
   (NFS/Longhorn). The Studio still pins to .33 for its file-store PVC — that's expected.

## Verified
Pushed `busybox:test` from .27, ran a pod pinned to **.28** with `imagePullPolicy: Always`
— it pulled cross-node from the registry and ran. ✅
