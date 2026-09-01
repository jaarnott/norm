# Dev box (GCE)

A permanent replacement for the Codespaces — one machine hosting every
project, instead of one Codespace per repo.

Its own root module with its own state, deliberately: the module in `../`
manages production, and its config has drifted from live before (see
`infra/COST-CUTS-2026-08.md`). `apply` here cannot touch any of that.

## What it builds

| | |
|---|---|
| `e2-standard-4` | 4 vCPU / 16 GB — same shape as the Codespace it replaces |
| 100 GB pd-balanced | keeps billing while stopped, so it sets the floor |
| No external IP | SSH only through Google's IAP forwarders |
| Cloud NAT | outbound egress — without it the startup script installs nothing |
| Attached service account | **no key file on disk** — this is the point |
| Idle shutdown | stops itself after 45 min unused |

Installed: Python 3.12, Node 24, pnpm, uv, Docker, git, gh, `cloud-sql-proxy`,
Playwright system libs, tmux, `unattended-upgrades`.

## Cost

E2 gets **no** sustained-use discount (only N1/N2/N2D/C2/M1/M2 do), so the
on-demand rate is the real one — ~USD $0.19/hr in Sydney.

| | Monthly |
|---|---|
| Always on | ~$139 |
| ~217 h (nights and weekends off) | ~$41 |
| Stopped, disk only | ~$10–20 |

The idle shutdown is what makes the middle row true. A commitment (1-year
$87/mo, 3-year $62/mo) bills whether the box runs or not, so it only pays if
you decide to leave it on.

## Standing it up

Two steps need your own credentials — this repo's service account has no
organisation and no billing access, so it cannot create projects:

```bash
# 1. Create the project and attach billing.
#    A separate project, NOT norm-production: client work runs on this box, and
#    it should not sit inside Norm's production project.
gcloud projects create norm-dev --name="Norm dev box"
gcloud billing projects link norm-dev --billing-account=<YOUR_BILLING_ACCOUNT_ID>
#    (gcloud billing accounts list — to find the id)

# 2. Two APIs must exist before the first apply. The provider sends
#    X-Goog-User-Project (so nothing has to be enabled in production to build
#    a dev box), which means Terraform's own calls are checked against THIS
#    project — including the calls that would enable them. Chicken-and-egg,
#    so once by hand:
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
  --project=<PROJECT_ID>

# 3. Apply.
cd infra/terraform/dev-vm
cp terraform.tfvars.example terraform.tfvars   # set project_id, owners, db_projects
terraform init
terraform apply
```

If the apply is run by a service account from ANOTHER project, note that
`billing_project` + `user_project_override` in `versions.tf` is what makes that
work. Without it the first failure is a misleading "IAM API has not been used
in project <number>" naming the *credential's* project, not this one.

Budget alert while you are there — the missing one is what caused the
August 2026 outage. Billing → Budgets & alerts → $150/mo, email at 50/80/100%.

## Using it

```bash
scripts/devbox.sh code     # start it and configure VS Code Remote-SSH
scripts/devbox.sh ssh      # start it and drop into a shell
scripts/devbox.sh status   # state, uptime, whether idle shutdown is armed
scripts/devbox.sh down     # stop it now
```

`code` writes the `~/.ssh/config` entry with the IAP ProxyCommand, which is
what lets VS Code reach a machine with no external IP. After that it is
**Remote-SSH → Connect to Host → `norm-dev...`**, then open a folder under
`~/projects/` — one VS Code window per project, all on the one box.

### Long unattended runs

The box shuts down after 45 minutes idle. "Idle" already accounts for logged-in
sessions, CPU load, and running `claude`/`pytest`/`vitest`/`next dev`/`uvicorn`
— so an agent working in a detached tmux keeps it alive. For anything that
those checks would miss:

```bash
keepawake on     # hold it open
keepawake        # what is it now
keepawake off    # re-arm
```

### Projects and agents

```
~/projects/norm
~/projects/<loaded-project>/…
```

One `git worktree` per concurrent agent. Several sessions sharing one checkout
is what let another session's push carry an unreviewed commit to production on
31 Aug 2026.

## Database access

No key file. The VM's service account holds `cloudsql.client` and
`cloudsql.instanceUser` in each project listed in `db_projects`, and
`cloud-sql-proxy` picks credentials up from the metadata server.

Run proxies under systemd with `Restart=always` rather than by hand — an
unsupervised proxy dying and staying dead is what made prod DB access flaky in
the Codespace, not the network path.
