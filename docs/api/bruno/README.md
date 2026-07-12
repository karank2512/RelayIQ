# RelayIQ Bruno Collection

Import this folder into [Bruno](https://www.usebruno.com/) (Open Collection → select this
directory). Set the environment first (`environments/local.bru`), log in with the `01-login`
request — it stores the token in a runtime variable used by every other request.

Demo credentials (local seed data only): `operator@demo.relayiq.test` / `relayiq-demo-password`.

The webhook request needs a fresh HMAC signature; generate one with:

```bash
cd apps/api && .venv/bin/python - <<'EOF'
import json, time
from relayiq.services.webhook_security import build_signature_header
body = json.dumps({
  "event": "enrichment.requested", "tenant_slug": "demo", "entity_type": "contact",
  "entity": {"work_email": "jordan.calloway@meridianrobotics.test", "full_name": "Jordan Calloway"},
  "requested_fields": ["job_title", "seniority"]
}, separators=(",", ":"))
print("Body:", body)
print("X-RelayIQ-Signature:", build_signature_header("dev_only_webhook_secret", int(time.time()), body.encode()))
EOF
```
