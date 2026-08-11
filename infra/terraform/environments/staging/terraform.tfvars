environment = "staging"
project_id  = "norm-staging"
region      = "australia-southeast1"
domain      = "staging.bettercallnorm.com"

# Cost cut 2026-08 (billing outage postmortem): staging was running a
# PRODUCTION-tier DB (db-custom-1-3840, ~$70/mo) and an always-on 2 vCPU API
# (~$165/mo) for an environment nobody visits. Micro DB + scale-to-zero API —
# staging exists to smoke the deploy path, not to hold capacity. Scheduled
# tasks may be torn down mid-run here with min=0; that is acceptable in
# staging. (Phase 2 decision: delete staging entirely.)
db_tier             = "db-f1-micro"
db_ha_enabled       = false
db_backup_retention = 3
db_disk_size        = 50

cloudrun_api_min    = 0
cloudrun_api_max    = 2
cloudrun_web_min    = 0
cloudrun_web_max    = 2
cloudrun_api_cpu    = "1"
cloudrun_api_memory = "1Gi"
cloudrun_web_cpu    = "1"
cloudrun_web_memory = "512Mi"

# Monitoring
alert_email    = "jaarnott@gmail.com"
enable_alerts  = true
