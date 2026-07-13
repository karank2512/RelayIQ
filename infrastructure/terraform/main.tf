# ============================================================================
# RelayIQ — Fly.io deployment TEMPLATE (NOT APPLIED)
# ============================================================================
# Honesty note: nothing in this directory has been `terraform apply`d or
# deployed anywhere. These files are reviewed templates only.
#
# Why null_resource + flyctl instead of a Fly Terraform provider:
# Fly.io has no official, maintained Terraform provider. The community
# provider ("fly-apps/fly", originally dov/DAlperin's) is archived and
# unmaintained, lags the Machines API, and Fly's own docs steer users to
# flyctl / the Machines API instead. Rather than pin infrastructure to an
# abandoned provider, this config wraps flyctl in null_resource
# provisioners: Terraform still owns variable wiring, ordering, and
# create/destroy lifecycle, while flyctl (which Fly does maintain) does the
# actual work. If Fly ships a supported provider, swap these resources for
# real ones — the variables in variables.tf are already shaped for that.
#
# Prerequisites: flyctl installed and authenticated (`fly auth login`).
# Secrets are NEVER set here — see README.md ("Secrets") for the
# `fly secrets set` commands. No secret values appear in any .tf file.
# ============================================================================

terraform {
  required_version = ">= 1.6"

  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

locals {
  api_app    = "${var.app_name}-api"
  worker_app = "${var.app_name}-worker"
  # Repo root relative to this directory (used as the docker build context root).
  api_dir = "${path.module}/../../apps/api"
}

# ---------------------------------------------------------------------------
# API app: FastAPI + uvicorn, public HTTPS, health-checked on /readyz.
# Config lives in fly.api.toml (release_command runs `alembic upgrade head`).
# ---------------------------------------------------------------------------
resource "null_resource" "api_app" {
  triggers = {
    app    = local.api_app
    org    = var.fly_org
    region = var.region
    image  = var.api_image
    config = filesha256("${path.module}/fly.api.toml")
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"] # pipefail requires bash, not sh/dash
    command     = <<-EOT
      set -euo pipefail
      fly apps list --org ${var.fly_org} | grep -q "^${local.api_app}" \
        || fly apps create ${local.api_app} --org ${var.fly_org}
      fly deploy ${local.api_dir} \
        --app ${local.api_app} \
        --config ${abspath(path.module)}/fly.api.toml \
        --primary-region ${var.region} \
        ${var.api_image != "" ? "--image ${var.api_image}" : ""}
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "fly apps destroy ${self.triggers.app} --yes || true"
  }
}

# ---------------------------------------------------------------------------
# Worker app: Celery consumer for the enrichment + sync queues. Same image as
# the API; no public services. Deployed after the API so migrations (the API
# release_command) have already run against the shared database.
# ---------------------------------------------------------------------------
resource "null_resource" "worker_app" {
  depends_on = [null_resource.api_app]

  triggers = {
    app    = local.worker_app
    org    = var.fly_org
    region = var.region
    image  = var.worker_image
    config = filesha256("${path.module}/fly.worker.toml")
  }

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"] # pipefail requires bash, not sh/dash
    command     = <<-EOT
      set -euo pipefail
      fly apps list --org ${var.fly_org} | grep -q "^${local.worker_app}" \
        || fly apps create ${local.worker_app} --org ${var.fly_org}
      fly deploy ${local.api_dir} \
        --app ${local.worker_app} \
        --config ${abspath(path.module)}/fly.worker.toml \
        --primary-region ${var.region} \
        ${var.worker_image != "" ? "--image ${var.worker_image}" : ""}
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "fly apps destroy ${self.triggers.app} --yes || true"
  }
}

# ---------------------------------------------------------------------------
# Postgres: intentionally NOT modeled as a resource.
# Managed database creation is a one-time, stateful operation that fits flyctl
# (or the Neon console) better than a provisioner that Terraform might re-run:
#   fly postgres create --name <app>-db --region <region>   # Fly Postgres
#   ...or use Neon's free tier (https://neon.tech) and take its connection URL.
# Wire the resulting URL in as the DATABASE_URL secret (README.md, "Secrets").
# Redis: use Upstash (free tier) via `fly redis create` or upstash.com, then
# set REDIS_URL / CELERY_BROKER_URL / CELERY_RESULT_BACKEND secrets.
# ---------------------------------------------------------------------------

output "api_app_name" {
  value = local.api_app
}

output "worker_app_name" {
  value = local.worker_app
}
