# Norm Infrastructure — Terraform

Deploys the full Norm stack to GCP using Cloud Run, Cloud SQL, and Secret Manager.

## Architecture

```
HTTPS LB (bettercallnorm.com)
├── /api/*  → Cloud Run: norm-api
└── /*      → Cloud Run: norm-web
                  │
            VPC Connector
                  │
            Cloud SQL (PostgreSQL 16)
```

## Environments

| Environment | Project | Domain | DB | Scaling |
|---|---|---|---|---|
| testing | norm-testing | testing.bettercallnorm.com | db-f1-micro | 0-2 (scale to zero) |
| staging | norm-staging | staging.bettercallnorm.com | db-custom-1-3840 | 0-3 (scale to zero) |
| production | norm-production | bettercallnorm.com | db-custom-4-15360 HA | 1-10 (always warm) |

## Prerequisites

1. Three GCP projects: `norm-testing`, `norm-staging`, `norm-production`
2. A GCS bucket for Terraform state: `norm-tfstate-491101`
3. `gcloud` CLI authenticated with owner access to all three projects
4. DNS for `bettercallnorm.com` pointed to GCP (update NS records after first apply)

## First-Time Setup

```bash
# 1. Create the Terraform state bucket (one-time, in any project)
gcloud storage buckets create gs://norm-tfstate-491101 \
  --project=norm-production \
    --location=australia-southeast1

# 2. Deploy testing environment
cd environments/testing
terraform init -backend-config=backend.tf
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars

# 3. Set secrets (after infrastructure is created)
echo -n "your-jwt-secret" | gcloud secrets versions add JWT_SECRET --data-file=- --project=norm-testing
echo -n "your-anthropic-key" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=- --project=norm-testing
# ... repeat for STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, SENTRY_DSN

# 4. Repeat for staging and production
```

## Adding a New Environment

1. Create `environments/<name>/terraform.tfvars` (copy from testing, adjust)
2. Create `environments/<name>/backend.tf` (change prefix)
3. Create a GCP project for it
4. `terraform init && terraform apply`
5. Add GitHub Actions environment with secrets
6. Add DNS record

No code changes required.

## Day-to-Day (via CI/CD)

You don't need to run Terraform manually after initial setup. The CI/CD pipeline
(`.github/workflows/deploy.yml`) handles:
- Building Docker images
- Pushing to Artifact Registry
- Running migrations
- Deploying to Cloud Run
- Smoke testing

Production deploys are triggered from the Norm admin panel (Settings → Deployments → Promote).
