environment = "production"
project_id  = "norm-production-491101"
region      = "australia-southeast1"
domain      = "bettercallnorm.com"

# Database — !! TERRAFORM IS NOT ALIGNED WITH LIVE (2026-08-09) !!
# Production now runs on instance `norm-prod-db` (db-g1-small, 20 GB), migrated
# from `norm-production` (200 GB, now STOPPED as a fallback — delete it after a
# stable week, its disk still bills ~$37/mo while it exists). The database
# module still declares name "norm-${environment}" = the OLD instance: DO NOT
# `terraform apply` production until the module is pointed at norm-prod-db and
# the new instance is imported into state (see infra/COST-CUTS-2026-08.md).
db_tier             = "db-g1-small"
db_ha_enabled       = false
db_backup_retention = 7
db_disk_size            = 20
db_read_replica_enabled = false
db_cross_region_backup  = "australia-southeast2"
# PITR is currently disabled on the live instance; toggling it forces a restart,
# so keep Terraform aligned with reality rather than flipping it on an apply.
db_point_in_time_recovery = false

# Cloud Run — API keeps 1 warm instance so scheduled-task background execution
# isn't torn down between requests; web can scale to zero.
# Cost cut 2026-08 (billing outage postmortem): 2 vCPU/2Gi always-on was
# ~$165/mo serving health checks — 1 vCPU/1Gi halves the always-on burn
# (~$80/mo saved) with zero measurable traffic to notice. Max capped at 3:
# runaway autoscale on this budget is a bill, not a feature.
cloudrun_api_min    = 1
cloudrun_api_max    = 3
cloudrun_web_min    = 0
cloudrun_web_max    = 2
cloudrun_api_cpu    = "1"
cloudrun_api_memory = "1Gi"
cloudrun_web_cpu    = "1"
cloudrun_web_memory = "512Mi"

# Monitoring
alert_email    = "jaarnott@gmail.com"
enable_alerts  = true
