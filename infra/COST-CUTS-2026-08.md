# Cost cuts — 2026-08 billing outage postmortem

Context: billing was disabled on the shared billing account on 2026-08-08
(~18:02 UTC) taking all three environments down. Root finding: infrastructure
has cost ~$700/mo since March — GCP free-trial credits masked it until June.
Traffic reality (last healthy hour of production): 372 health checks, 62
scheduler ticks, zero human requests.

## Already applied (work even with billing disabled — SQL Admin accepts writes)

- `norm-config` availability REGIONAL → **ZONAL** (halves it: ~$36/mo saved).
- `norm-production` storage auto-grow **disabled** (disk frozen at 200 GB —
  shrinking requires migration, see phase 2).
- Terraform tfvars in this repo now carry the downsized shapes for all three
  environments (production API 1 vCPU/1 GiB max 3; staging micro DB +
  scale-to-zero; testing scale-to-zero). Deploys only swap images, so these
  land via `terraform apply` or the one-liners below.

## The moment billing is re-enabled

1. Re-enable: https://console.developers.google.com/billing/enable?project=norm-production-491101
   (same billing account covers norm-staging and norm-testing).

2. Production Cloud Run downsize — takes effect instantly, no deploy:

   ```bash
   gcloud run services update norm-api-production \
     --project=norm-production-491101 --region=australia-southeast1 \
     --cpu=1 --memory=1Gi --max-instances=3 --quiet
   ```

3. Verify production: `curl -s -o /dev/null -w "%{http_code}" https://bettercallnorm.com/api/health` → 200.

4. Staging + testing (the Codespaces service account cannot reach these
   projects — run with owner credentials, or `terraform apply` per env):

   ```bash
   gcloud run services update norm-api-staging \
     --project=norm-staging --region=australia-southeast1 \
     --cpu=1 --memory=1Gi --min-instances=0 --max-instances=2 --quiet
   gcloud sql instances patch <staging-sql-instance> \
     --project=norm-staging --tier=db-f1-micro --quiet
   gcloud run services update norm-api-testing \
     --project=norm-testing --region=australia-southeast1 \
     --min-instances=0 --quiet
   ```

5. Budget alert (billing-account admin only, console):
   Billing → Budgets & alerts → create budget, $150/mo, email at 50/80/100%.
   This outage WAS the missing alert.

## Phase 2 — applied 2026-08-09

- **Prod DB tier** db-custom-1-3840 → **db-g1-small** (~$45/mo). Patched live.
- **Prod DB disk 200 GB → 20 GB** (~$34/mo once the old instance is deleted):
  full export was 5.3 MiB compressed. New instance **`norm-prod-db`**
  (POSTGRES_16, g1-small, 20 GB SSD, auto-grow on, ZONAL, same VPC, backups
  7d, IAM auth on + github-deploy IAM user). All 52 tables row-count-verified
  against the source, DATABASE_URL secret v3 + Cloud Run/migrate-job
  annotations repointed, login endpoint verified reading the new DB.
- **Old `norm-production` instance: STOPPED, kept as fallback.** Its 200 GB
  disk still bills (~$37/mo) until deleted — **delete after a stable week**:
  `gcloud sql instances delete norm-production --project=norm-production-491101`
  Also delete the migration bucket then:
  `gcloud storage rm -r gs://norm-production-db-migration`
- **TERRAFORM DRIFT**: modules/database names the instance "norm-${environment}"
  (the old one). Before any production `terraform apply`: point the module at
  `norm-prod-db` (name var or rename) and `terraform import` it into state.

## Still open

| Item | Saves/mo | Who |
|---|---|---|
| Budget alert $150/mo (50/80/100% emails) | prevents outages | billing admin (user) |
| Artifact Registry cleanup (norm-testing) — SA has no access; run in Cloud Shell: | growth | user or grant `roles/artifactregistry.admin` |

```bash
cat > /tmp/cleanup.json <<'EOF'
[
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "30d", "tagState": "any"}},
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 10}}
]
EOF
gcloud artifacts repositories set-cleanup-policies norm \
  --project=norm-testing --location=australia-southeast1 \
  --policy=/tmp/cleanup.json --no-dry-run
```

- Prod API scale-to-zero (~$80/mo): app change — run scheduled tasks
  in-request so min=0 is safe. Largest remaining lever.
- Drop staging+testing LBs (~$40/mo): terraform flag + E2E to run.app URLs.
- Staging kept (user decision) at ~$40/mo floor.

Run rate after today: **~$210/mo** (~$175 once the old instance is deleted),
down from ~$700.
