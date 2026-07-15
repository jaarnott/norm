environment = "production"
project_id  = "norm-production-491101"
region      = "australia-southeast1"
domain      = "bettercallnorm.com"

# Database — right-sized for early stage, scale up when needed
db_tier             = "db-custom-1-3840"
db_ha_enabled       = false
db_backup_retention = 7
db_disk_size            = 200
db_read_replica_enabled = false
db_cross_region_backup  = "australia-southeast2"
# PITR is currently disabled on the live instance; toggling it forces a restart,
# so keep Terraform aligned with reality rather than flipping it on an apply.
db_point_in_time_recovery = false

# Cloud Run — API keeps 1 warm instance so scheduled-task background execution
# isn't torn down between requests; web can scale to zero. CPU/memory/max match
# the hand-tuned live service so an apply doesn't downsize it.
cloudrun_api_min    = 1
cloudrun_api_max    = 10
cloudrun_web_min    = 0
cloudrun_web_max    = 2
cloudrun_api_cpu    = "2"
cloudrun_api_memory = "2Gi"
cloudrun_web_cpu    = "1"
cloudrun_web_memory = "512Mi"

# Monitoring
alert_email    = "jaarnott@gmail.com"
enable_alerts  = true
