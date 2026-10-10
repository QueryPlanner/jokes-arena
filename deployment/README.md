# VM deployment

The production image contains the frontend and Python REST service. Gunicorn
serves both on port 8080. Caddy forwards `jokesarena.lordpatil.com` to
`127.0.0.1:8183`. Compose runs as UID 1000 and stores SQLite in `./data/arena.db`.
The app does not need an OpenRouter key to serve the pre-generated pool.

## Delivery flow

Pull requests run Python tests with 100% statement coverage, frontend event
tests, and an actual Docker voting smoke test. After a reviewed merge to protected
`main`, Actions repeats validation and publishes an immutable image to
`ghcr.io/queryplanner/jokes-arena`. Deployment remains disabled until setup is
complete. Manual workflows can publish only from `main`.

The production job joins Tailscale and connects to `appuser@100.124.202.79`.
Its repository-specific SSH key can execute only `deploy sha256:<digest> <revision>`.
The host verifies image source and revision labels, checks port ownership, backs
up SQLite, and starts Compose. Readiness must report the expected revision and
at least one eligible pair. Failure restores the prior image and retains the
current voting database. A first deployment seeds an exclusive staging database
and publishes it only after validation. Seed failures remain retryable.

Deployment support files are installed separately in
`/home/appuser/apps/jokes-arena`. The restricted job cannot replace its own SSH
gate or Compose file. Changes to those files require a separate host installation.
Backups stay in `backups/`; they currently have no automatic retention policy.

## GitHub configuration

Protect `main` with required pull requests and the `Validate` status check.
Restrict the `production` environment to protected branches. Set:

| Name | Type | Value |
| --- | --- | --- |
| `DEPLOY_SSH_KEY` | Secret | New repository-specific private SSH key |
| `DEPLOY_KNOWN_HOSTS` | Variable | Verified VM ED25519 host key |
| `TS_CLIENT_ID` | Variable | Repository-specific Tailscale federation client ID |
| `TS_AUDIENCE` | Variable | Audience supplied by Tailscale |
| `DEPLOY_ENABLED` | Variable | `false` until host and federation setup pass |

Use Tailscale workload identity federation to avoid a long-lived OAuth secret.
Create the identity with issuer `https://token.actions.githubusercontent.com`
and subject `repo:QueryPlanner/jokes-arena:environment:production`. Allow only
Auth Keys write scope and tag `tag:jokesarena-ci`.

Review the existing tailnet policy before adding a grant from `tag:jokesarena-ci`
to `100.124.202.79` on `tcp:22`. Grants are additive; an existing broad grant can
still allow additional access. Do not replace unrelated rules. The VM uses normal
OpenSSH, so the restricted authorized key applies. Tailscale SSH is disabled.

References: [Tailscale GitHub action](https://tailscale.com/docs/integrations/github/github-action)
and [workload identity federation](https://tailscale.com/docs/features/workload-identity-federation).

## Host installation

Install `compose.yaml`, `deployment/deploy.py`, and
`deployment/check-host-port.sh` beneath the app directory. Preserve existing
files when upgrading. Put the validated private receipt in `seed/receipt.jsonl`
with directory mode 700 and file mode 600. Keep it outside Git and image builds.
Do not copy the local voting database over an existing production database.

Append the repository public key to `~/.ssh/authorized_keys` with this prefix:

```text
restrict,command="/usr/bin/python3 /home/appuser/apps/jokes-arena/deployment/deploy.py"
```

Append only the reviewed Caddy fragment to a candidate containing the existing
configuration. Validate the candidate before privileged installation, preserve a
backup, reload Caddy, and restore the backup if reload fails. The host owner must
run the privileged installation. Do not replace existing site blocks.

Create a proxied Cloudflare A record for `jokesarena.lordpatil.com` pointing to
`135.181.98.66` after the origin is ready. Verify HTTPS and `/api/ready` through
the public domain. DNS configuration alone does not prove a successful deployment.

## Verification and rollback

```sh
python -m compileall -q arena scripts deployment
python -m coverage run --source=arena,scripts,deployment -m unittest discover -s tests
python -m coverage report --fail-under=100 -m
node --test tests/frontend.test.cjs
REVISION=$(git rev-parse HEAD)
docker build --build-arg REVISION="$REVISION" --tag jokes-arena:test .
bash deployment/smoke.sh jokes-arena:test "$REVISION"
bash deployment/check-host-port.sh 8183 jokes-arena
```

The smoke test uses an isolated synthetic database and random localhost port.
It verifies frontend serving, readiness, anonymous assignments, vote persistence,
model reveal, leaderboard totals, and private-file rejection. It never calls a
model provider or votes in production. Python regression tests exercise rollback
with mocked Docker commands and a real SQLite WAL database.

`current.json` records the last verified image and revision. The SSH gate can
redeploy that digest, using a short-lived registry credential on standard input
if needed. Never restore a backup over a live database without stopping writers
and explicitly reviewing the votes that would be lost.
