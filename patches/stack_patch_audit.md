# BIOMERO Stack Patch Audit

This audit separates generally useful stack extensions from deployment-specific
policy and cleanup candidates. It complements `slurm_spider_patch_audit.md`,
which covers the Slurm/BIOMERO worker integration in more detail.

## Generally Useful Extensions

- Containerized full OMERO/BIOMERO stack: `docker-compose.yml` consistently
  runs OMERO.server, OMERO.web, a BIOMERO processor, BIOMERO analytics storage,
  Metabase, and the optional importer on one host.
- Version-pinned application surface: `.env.shared` centralizes the pinned
  versions for BIOMERO, OMERO.biomero, OMERO.forms, OMERO Zarr pixel buffer,
  and BIOMERO.importer.
- Separate BIOMERO analytics database: the `database-biomero` service keeps
  analytics/import tracking away from the OMERO database and uses environment
  URLs for worker/web/importer alignment.
- OMERO.web integration bundle: `web/Dockerfile` installs OMERO.biomero,
  OMERO.forms, OMERO.figure, iviewer, mapr, parade, and tagging tools in one
  reproducible web image.
- Reverse-proxy-aware BIOMERO analytics links: `web/patch_biomero_web_runtime.py`
  keeps embedded Metabase links under the public OMERO origin instead of
  hard-coded localhost URLs.
- OMERO.forms bootstrap: `web/44-create_forms_user.py` and
  `web/45-fix-forms-config.sh` make forms setup reproducible across rebuilds.
- BIOMERO script patches on OMERO.server: `server/patch_biomero_scripts_runtime.py`
  fixes stale input-data choices, failed import propagation, and metadata-based
  attachment lookup. These are upstreamable BIOMERO behavior fixes, not
  Spider-specific.
- Zarr rendering support: `server/Dockerfile` installs the matching
  `omero-zarr-pixel-buffer` runtime jars and dependencies from the configured
  version.
- Project-local SSH material for containers: `scripts/write-project-ssh-from-env.sh`
  and `biomeroworker/10-mount-ssh.sh` keep the worker's SSH layout reproducible
  even when host SSH permissions are incompatible with container users.
- Public-secret lifecycle scripts: `scripts/write-clear-env-from-dotenvx.sh`,
  `scripts/rotate-public-secrets.sh`, and
  `scripts/write-nginx-htpasswd-from-env.sh` provide a repeatable path for
  encrypted committed secrets plus local clear-text deployment env.
- Host user access controls: `scripts/create-ssh-user.sh`,
  `scripts/delete-ssh-user.sh`, and `scripts/apply-user-lockdown.sh` are useful
  operational tooling for a shared VM, independent of Spider.
- Optional centralized logs: `opensearch-compose.yml` plus Fluent Bit parsers
  make the BIOMERO stack easier to diagnose without coupling application
  behavior to OpenSearch.

## Deployment-Specific Policy

- Spider identity and project paths: `.env.shared`,
  `scripts/render-slurm-config.sh`, `scripts/deploy-local-stack.sh`, and
  `web/slurm-config-template.ini` currently assume `SPIDER_USER`,
  `SPIDER_PROJECT`, `spider.surf.nl`, and `/project/<project>/Share/biomero`.
- Spider GPU policy: `BIOMERO_GPU_PARTITION`, `BIOMERO_GPUS`, and
  `BIOMERO_FORCE_GPU_WORKFLOWS` encode the Spider rule that GPU workflows must
  request `--gpus`, while CPU-only jobs should fall through to the normal
  default partition.
- SURF Research Cloud ingress: `nginx/omero-web.conf` is a host nginx location
  fragment for SURF-managed nginx, including `/metabase/` and `/logs` proxying.
  It is appropriate for this VM but not a generic standalone nginx config.
- Direct host port exposure: `docker-compose.yml` exposes OMERO, OMERO.web, and
  Metabase on host ports. That is convenient for local/SURF proxying, but a
  public deployment should restrict exposure with firewall or bind-to-localhost
  policy as needed.
- Importer privilege model: `biomero-importer` uses `privileged: true`,
  `/dev/fuse`, and disabled labeling because the importer image runs Podman
  inside the container. This is not Spider-specific, but it is a deliberate
  operational tradeoff.
- OpenSearch dashboards under `/logs`: the log viewer assumes a host proxy and
  basic-auth file. It should remain optional and separately started.

## Development-Only Scaffolding

- `docker-compose-dev.yml` intentionally mounts adjacent source checkouts for
  `OMERO.biomero` and `OMERO.forms`.
- `docker-compose-dev.yml` leaves `omeroweb` running `tail -f /dev/null` so a
  developer can enter the container and run web processes manually.
- The dev importer mounts both source and config/log paths for live iteration.
  This is useful locally but should not be used as the production stack.

## Cleanup Applied

- Removed obsolete top-level Compose `version: "3"` declarations from the main,
  dev, and Loki/Grafana compose files. Current Docker Compose ignores this field
  and emits warnings.
- Replaced the predictable world-writable `/tmp/forms-config` scratch directory
  with a private `mktemp -d` directory in `web/45-fix-forms-config.sh`.

## Remaining Cleanup Candidates

- Split Spider/SURF values from generic defaults more clearly. The current
  `.env.shared` is a valid deployment env for this VM, but a reusable template
  should use placeholder values and put Spider values in a deployment overlay.
- `scripts/deploy-local-stack.sh` still has broad `chmod -R 777` calls for
  `web/L-Drive` and logs. This works around mixed host/container users, but ACL
  or targeted ownership would be cleaner once all runtime writer UIDs are fixed.
- `scripts/deploy-local-stack.sh` writes a `localslurm` SSH host with
  `StrictHostKeyChecking no`. That host is legacy/local-development scaffolding
  and can be removed if this branch is Spider-only.
- Consider binding public service ports to `127.0.0.1` when SURF nginx is the
  only intended public ingress.
- Consider moving the optional logging stack behind a compose profile or a
  documented startup command so orphan containers are less likely after normal
  stack restarts.