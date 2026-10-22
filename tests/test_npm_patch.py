import json

import pytest

from liara_agent.heal.apply import apply_npm_dependency, npm_latest


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def pkg(tmp_path, **deps):
    (tmp_path / "package.json").write_text(json.dumps({"name": "x", "dependencies": deps}, indent=2))
    return tmp_path


def test_adds_the_dependency_with_a_caret_and_keeps_the_list_sorted(tmp_path):
    root = pkg(tmp_path, zeta="1.0.0", alpha="2.0.0")
    diff = apply_npm_dependency(root, "express-rate-limit", lookup=lambda p: "7.4.0")
    assert json.loads((root / "package.json").read_text())["dependencies"] == {"alpha": "2.0.0", "express-rate-limit": "^7.4.0", "zeta": "1.0.0"}
    assert "+    \"express-rate-limit\": \"^7.4.0\"" in diff


def test_already_declared_is_not_touched(tmp_path):
    root = pkg(tmp_path, express="4.0.0")
    assert apply_npm_dependency(root, "express", lookup=lambda p: pytest.fail("no lookup needed")) is None


def test_registry_answers():
    assert npm_latest("left-pad", get=lambda url: Resp(200, {"version": "1.3.0"})) == "1.3.0"
    seen = []
    assert npm_latest("@scope/pkg", get=lambda url: seen.append(url) or Resp(200, {"version": "2.0.0"})) == "2.0.0"
    assert seen == ["https://registry.npmjs.org/@scope%2Fpkg/latest"]


def test_registry_problems_are_said_in_words():
    with pytest.raises(LookupError, match="does not exist"):
        npm_latest("nope-xyz", get=lambda url: Resp(404))
    with pytest.raises(LookupError, match="could not look up"):
        npm_latest("x", get=lambda url: (_ for _ in ()).throw(ConnectionError()))
