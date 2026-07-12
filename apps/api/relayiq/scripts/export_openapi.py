"""Export the OpenAPI document to a file: python -m relayiq.scripts.export_openapi <out.json>"""

import json
import sys
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    from relayiq.main import app

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2))
    print(f"wrote {out}")  # noqa: T201


if __name__ == "__main__":
    main()
