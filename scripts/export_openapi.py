#!/usr/bin/env python
"""Write docs/openapi.json from the real app, so the frontend can generate typed API clients. Run from the project root."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from liara_agent.config import Settings  # noqa: E402
from liara_agent.web.app import create_app  # noqa: E402
from liara_agent.web.config import WebSettings  # noqa: E402

app = create_app(WebSettings(data_dir=Path(tempfile.mkdtemp()), secret_key="x", static_dir=Path("/nonexistent")), None, Settings())
(ROOT / "docs" / "openapi.json").write_text(json.dumps(app.openapi(), indent=1))
print("wrote docs/openapi.json:", len(app.openapi()["paths"]), "paths")
