"""Root conftest: make the `relayiq` package importable regardless of how pytest is
invoked (bin/pytest vs python -m pytest) or whether the editable-install .pth was
processed — some sandboxed/homebrew Python environments skip .pth import lines."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
