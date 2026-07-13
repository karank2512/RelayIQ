# No environment-specific values are hardcoded here — everything deploy-specific
# comes in via -var / -var-file / TF_VAR_* (never commit real *.tfvars; .gitignore
# already excludes them).

variable "app_name" {
  description = "Base Fly.io app name; the api and worker apps are derived as <app_name>-api / <app_name>-worker."
  type        = string
  default     = "relayiq"
}

variable "fly_org" {
  description = "Fly.io organization slug that owns the apps."
  type        = string
  default     = "personal"
}

variable "region" {
  description = "Primary Fly.io region (e.g. iad, lhr, fra)."
  type        = string
  default     = "iad"
}

variable "api_image" {
  description = "Container image reference for the API (e.g. registry.fly.io/relayiq-api:v0.1.0). Empty string builds from apps/api/Dockerfile via the fly.api.toml [build] section."
  type        = string
  default     = ""
}

variable "worker_image" {
  description = "Container image reference for the Celery worker. Empty string builds from apps/api/Dockerfile via the fly.worker.toml [build] section. The api and worker share one image."
  type        = string
  default     = ""
}
