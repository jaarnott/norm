environment = "production"
project_id  = "norm-production-491101"
region      = "australia-southeast1"
domain      = "bettercallnorm.com"

# Database — production grade, HA
db_tier             = "db-custom-4-15360"
db_ha_enabled       = true
db_backup_retention = 30
db_disk_size        = 200

# Cloud Run — always warm
cloudrun_api_min    = 1
cloudrun_api_max    = 10
cloudrun_web_min    = 1
cloudrun_web_max    = 5
cloudrun_api_cpu    = "2"
cloudrun_api_memory = "2Gi"
cloudrun_web_cpu    = "1"
cloudrun_web_memory = "512Mi"
