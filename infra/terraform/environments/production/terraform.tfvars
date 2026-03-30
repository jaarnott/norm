environment = "production"
project_id  = "norm-production-491101"
region      = "australia-southeast1"
domain      = "bettercallnorm.com"

# Database — right-sized for early stage, scale up when needed
db_tier             = "db-custom-1-3840"
db_ha_enabled       = false
db_backup_retention = 7
db_disk_size            = 20
db_read_replica_enabled = false
db_cross_region_backup  = ""

# Cloud Run — scale to zero, right-sized for early stage
cloudrun_api_min    = 0
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
