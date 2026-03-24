environment = "staging"
project_id  = "norm-staging"
region      = "australia-southeast1"
domain      = "staging.bettercallnorm.com"

# Database — small, mirrors prod schema
db_tier             = "db-custom-1-3840"
db_ha_enabled       = false
db_backup_retention = 7
db_disk_size        = 50

# Cloud Run — scale to zero when idle
cloudrun_api_min    = 0
cloudrun_api_max    = 3
cloudrun_web_min    = 0
cloudrun_web_max    = 3
cloudrun_api_cpu    = "2"
cloudrun_api_memory = "2Gi"
cloudrun_web_cpu    = "1"
cloudrun_web_memory = "512Mi"

# Monitoring
alert_email    = "jaarnott@gmail.com"
enable_alerts  = true
