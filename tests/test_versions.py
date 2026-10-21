import json

import pytest

from liara_agent.detect.versions import choose, detect

NODE = ["14", "16", "18", "20", "22", "24"]
PY = ["3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]
PHP = ["8.4", "8.3", "8.2", "8.1", "8.0", "7.4", "7.3", "7.2"]


@pytest.mark.parametrize("spec,expected", [
    ("13.12.0", "14"),        # the real mern-marketplace pin: nothing offered satisfies it, so the nearest newer one
    ("8.11.1", "14"),
    ("16.13.0", "16"),
    ("^16.13.0", "16"),
    ("~18.2", "18"),
    ("18.x", "18"),
    (">=14", None),           # the default (22) already satisfies it
    (">=16 <20", "18"),       # newest offered inside the window
    ("^22", None),
    ("30", "24"),             # everything offered is older: the newest
    ("lts/*", None),
])
def test_node_requirement(spec, expected):
    assert choose(spec, NODE, "22") == expected


def test_python_requirements():
    assert choose("3.9.7", PY, "3.12") == "3.9"
    assert choose(">=3.9", PY, "3.12") is None
    assert choose("^3.8", PY, "3.12") is None
    assert choose("~=3.9.0", PY, "3.12") == "3.9"
    assert choose("==3.10.*", PY, "3.12") == "3.10"
    assert choose("3.6", PY, "3.12") == "3.7"


def test_php_requirements_use_composer_semantics():
    assert choose("^7.4 || ^8.0", PHP, "8.2", composer=True) is None      # 8.2 is allowed by the second alternative
    assert choose("^7.4", PHP, "8.2", composer=True) == "7.4"
    assert choose("~7.4", PHP, "8.2", composer=True) == "7.4"             # composer: ~7.4 means >=7.4 <8.0
    assert choose(">=7.2 <8.1", PHP, "8.2", composer=True) == "8.0"


def test_go_directive_is_a_floor():
    GO = ["1.21", "1.22", "1.23", "1.24", "1.25"]
    assert choose("1.22.1", GO, "1.24", minimum=True) is None
    assert choose("1.20", GO, "1.24", minimum=True) is None
    assert choose("1.26", GO, "1.24", minimum=True) == "1.25"


def test_detect_reads_the_right_file_and_uses_the_right_key(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"engines": {"node": "13.12.0"}}))
    d = detect("node", tmp_path)
    assert d.config == {"version": "14"} and d.source == "package.json engines.node" and d.wanted == "13.12.0"
    assert detect("next", tmp_path).config == {"version": "20"}  # the next platform offers 20, 22, 24

    (tmp_path / "runtime.txt").write_text("python-3.9.18\n")
    assert detect("django", tmp_path).config == {"pythonVersion": "3.9"} and detect("python", tmp_path).config == {"version": "3.9"}

    (tmp_path / "composer.json").write_text(json.dumps({"require": {"php": "^7.4"}}))
    assert detect("laravel", tmp_path).config == {"phpVersion": "7.4"} and detect("php", tmp_path).config == {"version": "7.4"}

    (tmp_path / "go.mod").write_text("module x\n\ngo 1.26\n")
    assert detect("go", tmp_path).config == {"version": "1.25"}

    (tmp_path / "App.csproj").write_text("<Project><PropertyGroup><TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>")
    assert detect("dotnet", tmp_path).config == {"version": "3.1"}


def test_nothing_is_sent_when_the_project_says_nothing_or_the_default_fits(tmp_path):
    assert detect("node", tmp_path) is None and detect("docker", tmp_path) is None
    (tmp_path / "package.json").write_text(json.dumps({"engines": {"node": ">=18"}}))
    assert detect("node", tmp_path) is None


def test_engine_conflict_assumes_the_newest_patch_level(tmp_path):
    from liara_agent.detect.versions import engine_conflict
    for pin, major, conflict in (("13.12.0", 14, True), ("16.13.0", 16, True), ("~18.2", 18, True), ("^16.13.0", 16, False), (">=14", 22, False), ("18.x", 18, False), ("14 || 16", 16, False)):
        (tmp_path / "package.json").write_text(json.dumps({"engines": {"node": pin}}))
        assert (engine_conflict(tmp_path, major) == pin) is conflict, pin
    assert engine_conflict(tmp_path / "missing", 22) is None
