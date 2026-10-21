import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from liara_agent.liara import catalog

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def test_catalog_has_expected_platforms():
    assert {"node", "next", "django", "python", "vue", "docker", "static"} <= catalog.platforms()
    assert catalog.default_port("go") == 8080


@pytest.mark.skipif(not shutil.which("liara") or not shutil.which("node"), reason="liara CLI not installed")
def test_generated_catalog_matches_installed_cli():
    """Fails when the CLI was upgraded but catalog.generated.json wasn't regenerated."""
    import gen_catalog

    assert gen_catalog.generate() == catalog.catalog(), "run: python scripts/gen_catalog.py"
