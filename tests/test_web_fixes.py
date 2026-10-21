from liara_agent.web import sources

PATCH = "--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1,2 @@\n # none\n+flask\n"


def test_the_agents_saved_fix_is_put_back_on_a_fresh_download(tmp_path):
    repo, patch = tmp_path / "repo", tmp_path / "fixes.patch"
    repo.mkdir()
    (repo / "requirements.txt").write_text("# none\n")
    patch.write_text(PATCH)
    assert sources.apply_saved_fixes(repo, patch) == "applied"
    assert (repo / "requirements.txt").read_text() == "# none\nflask\n"
    assert sources.apply_saved_fixes(repo, patch) == "already"        # the repository has it now (merged, or applied before)


def test_a_repository_that_changed_those_lines_is_left_alone(tmp_path):
    repo, patch = tmp_path / "repo", tmp_path / "fixes.patch"
    repo.mkdir()
    (repo / "requirements.txt").write_text("django\n")
    patch.write_text(PATCH)
    assert sources.apply_saved_fixes(repo, patch) == "conflict" and (repo / "requirements.txt").read_text() == "django\n"


def test_no_saved_fix_means_nothing_to_do(tmp_path):
    assert sources.apply_saved_fixes(tmp_path, tmp_path / "missing.patch") is None
