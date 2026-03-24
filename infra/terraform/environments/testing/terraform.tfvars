environment = "testing"
project_id  = "norm-testing"
region      = "australia-southeast1"
domain      = "testing.bettercallnorm.com"

# Database — smallest, cheapest
db_tier             = "db-f1-micro"
db_ha_enabled       = false
db_backup_retention = 3
db_disk_size        = 10

# Cloud Run — scale to zero
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
enable_alerts  = false
