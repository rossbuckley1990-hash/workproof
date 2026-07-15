"""Tests for test-weakening heuristics.

Per the style guide (DECISIONS.md D12): every heuristic ships one true
positive AND one known false positive as fixture tests, the latter
documenting the documented limitation in the test docstring.

Each test builds a tiny git repo in tmp_path with a base commit and a head
commit, then runs :func:`workproof.heuristics.analyze_diff` and asserts on
the counts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workproof.heuristics import (
    analyze_diff,
    counts_by_file,
    detect_language,
    is_test_file,
)

# ----- helpers -----


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for path, content in files.items():
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


# ----- language detection -----


class TestDetectLanguage:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("test_app.py", "python"),
            ("src/app.py", "python"),
            ("app.test.js", "javascript"),
            ("app.spec.ts", "javascript"),
            ("foo_test.go", "go"),
            ("README.md", None),
            ("Makefile", None),
        ],
    )
    def test_detect(self, path: str, expected: str | None) -> None:
        assert detect_language(path) == expected


class TestIsTestFile:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("test_app.py", True),
            ("tests/test_app.py", True),
            ("app_test.py", True),
            ("app.py", False),
            ("app.test.js", True),
            ("app.spec.tsx", True),
            ("test-runner.js", False),  # 'test-runner' has no dot before 'test'
            ("foo_test.go", True),
            ("main.go", False),
        ],
    )
    def test_is_test_file(self, path: str, expected: bool) -> None:
        assert is_test_file(path) is expected


# ----- Python: assertion removal -----


class TestPythonAssertionRemoval:
    def test_true_positive_assertion_removed(self, tmp_path: Path) -> None:
        """TRUE POSITIVE: a real assertion (`assert x == y`) is removed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(repo, {"test_app.py": "def test_x():\n    assert 1 == 1\n"}, "base")
        head = _commit(repo, {"test_app.py": "def test_x():\n    pass\n"}, "head")
        result = analyze_diff(repo, base, head, ["test_app.py"])
        assert result.assertions_removed == 1
        assert result.assertions_removed_details[0]["file"] == "test_app.py"

    def test_known_false_positive_commented_assertion(self, tmp_path: Path) -> None:
        """KNOWN FALSE POSITIVE: commenting out an assertion (`# assert ...`)
        is detected as a removal because the diff line starts with `-` and
        matches the assertion regex. The assertion wasn't *removed* — it was
        *disabled* — but Workproof can't tell the difference from a diff alone.

        Mitigation: the reviewer sees the file:line anchor and can check
        whether the line was deleted or commented. Future versions could
        parse the new version of the file to check for the comment.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"test_app.py": "def test_x():\n    assert 1 == 1\n"},
            "base",
        )
        head = _commit(
            repo,
            {"test_app.py": "def test_x():\n    # assert 1 == 1\n    pass\n"},
            "head",
        )
        result = analyze_diff(repo, base, head, ["test_app.py"])
        # FP: counted as removed, but actually commented out
        assert result.assertions_removed == 1

    def test_true_negative_assertion_added(self, tmp_path: Path) -> None:
        """Adding an assertion is not counted as a removal."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(repo, {"test_app.py": "def test_x():\n    pass\n"}, "base")
        head = _commit(repo, {"test_app.py": "def test_x():\n    assert 1 == 1\n"}, "head")
        result = analyze_diff(repo, base, head, ["test_app.py"])
        assert result.assertions_removed == 0

    def test_true_negative_assertion_modified_in_place(self, tmp_path: Path) -> None:
        """Modifying an assertion (remove old line + add new line) is detected
        as a removal. This is a known limitation but documented here as a
        boundary case: the count is non-zero but the *intent* was not weakening."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"test_app.py": "def test_x():\n    assert x == 1\n"},
            "base",
        )
        head = _commit(
            repo,
            {"test_app.py": "def test_x():\n    assert x == 2\n"},
            "head",
        )
        result = analyze_diff(repo, base, head, ["test_app.py"])
        # The old assert line is removed (counted) AND a new one is added (not counted).
        # Reviewer sees 1 removal + must check manually whether the new assertion
        # is equivalent or weaker.
        assert result.assertions_removed == 1


# ----- Python: skip/xfail markers -----


class TestPythonSkipMarkers:
    def test_true_positive_new_skip_marker(self, tmp_path: Path) -> None:
        """TRUE POSITIVE: a new @pytest.mark.skip decorator is added."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"test_app.py": "def test_x():\n    assert 1 == 1\n"},
            "base",
        )
        head = _commit(
            repo,
            {
                "test_app.py": "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    assert 1 == 1\n"
            },
            "head",
        )
        result = analyze_diff(repo, base, head, ["test_app.py"])
        assert result.new_skip_markers == 1

    def test_true_positive_new_xfail_marker(self, tmp_path: Path) -> None:
        """TRUE POSITIVE: a new @pytest.mark.xfail decorator is added."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"test_app.py": "def test_x():\n    assert 1 == 1\n"},
            "base",
        )
        head = _commit(
            repo,
            {
                "test_app.py": "import pytest\n\n@pytest.mark.xfail\ndef test_x():\n    assert 1 == 1\n"
            },
            "head",
        )
        result = analyze_diff(repo, base, head, ["test_app.py"])
        assert result.new_skip_markers == 1

    def test_known_false_positive_skip_in_string(self, tmp_path: Path) -> None:
        """KNOWN FALSE POSITIVE: the regex ``^\\s*pytest\\.skip\\(`` matches
        a line inside a multi-line string that happens to start with
        ``pytest.skip(`` after whitespace. We don't parse Python AST, so we
        can't tell it's inside a triple-quoted string.

        Mitigation: the reviewer sees the file:line anchor and can verify
        whether the call is real or in a string. A future version could use
        the ``ast`` module to filter false positives.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"test_app.py": "def test_x():\n    assert 1 == 1\n"},
            "base",
        )
        head = _commit(
            repo,
            {
                "test_app.py": (
                    "def test_x():\n"
                    '    """docs:\n'
                    "    pytest.skip() is how you disable a test\n"
                    '    """\n'
                    "    assert 1 == 1\n"
                )
            },
            "head",
        )
        result = analyze_diff(repo, base, head, ["test_app.py"])
        # FP: the line inside the docstring matches ^\s*pytest\.skip\(
        assert result.new_skip_markers == 1


# ----- JavaScript/TypeScript -----


class TestJavaScriptHeuristics:
    def test_true_positive_expect_removed(self, tmp_path: Path) -> None:
        """TRUE POSITIVE: a jest/vitest expect() call is removed."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"app.test.js": "test('x', () => {\n  expect(1).toBe(1);\n});\n"},
            "base",
        )
        head = _commit(
            repo,
            {"app.test.js": "test('x', () => {\n});\n"},
            "head",
        )
        result = analyze_diff(repo, base, head, ["app.test.js"])
        assert result.assertions_removed == 1

    def test_true_positive_it_skip_added(self, tmp_path: Path) -> None:
        """TRUE POSITIVE: it.skip(...) is added."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"app.test.js": "it('x', () => {});\n"},
            "base",
        )
        head = _commit(
            repo,
            {"app.test.js": "it.skip('x', () => {});\n"},
            "head",
        )
        result = analyze_diff(repo, base, head, ["app.test.js"])
        assert result.new_skip_markers == 1

    def test_known_false_positive_expect_in_comment(self, tmp_path: Path) -> None:
        """KNOWN FALSE POSITIVE: `expect(` matched inside a JS comment. We
        don't parse JS, so a doc-comment that mentions expect() will be
        flagged if the comment line is later removed.

        Mitigation: reviewer checks the anchor; future versions could use a
        JS parser to filter.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {"app.test.js": "test('x', () => {});\n// TODO: add expect(result).toBe(2)\n"},
            "base",
        )
        head = _commit(
            repo,
            {"app.test.js": "test('x', () => {});\n"},
            "head",
        )
        result = analyze_diff(repo, base, head, ["app.test.js"])
        # FP: the removed line is a comment mentioning expect(), counted as an assertion
        assert result.assertions_removed == 1


# ----- Go -----


class TestGoHeuristics:
    def test_true_positive_t_skip_added(self, tmp_path: Path) -> None:
        """TRUE POSITIVE: t.Skip() is added at the start of a Go test."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {
                "app_test.go": 'package app\n\nfunc TestX(t *testing.T) {\n  if 1 != 1 {\n    t.Fatal("x")\n  }\n}\n'
            },
            "base",
        )
        head = _commit(
            repo,
            {
                "app_test.go": 'package app\n\nfunc TestX(t *testing.T) {\n  t.Skip("flaky")\n  if 1 != 1 {\n    t.Fatal("x")\n  }\n}\n'
            },
            "head",
        )
        result = analyze_diff(repo, base, head, ["app_test.go"])
        assert result.new_skip_markers == 1

    def test_known_false_positive_t_skip_in_string(self, tmp_path: Path) -> None:
        """KNOWN FALSE POSITIVE: `t.Skip(` matched inside a Go string literal.

        Mitigation: reviewer checks the anchor; Go AST parsing would fix it
        but is out of scope for v0.1.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {
                "app_test.go": 'package app\n\nfunc TestX(t *testing.T) {\n  msg := "x"\n  _ = msg\n}\n'
            },
            "base",
        )
        head = _commit(
            repo,
            {
                "app_test.go": 'package app\n\nfunc TestX(t *testing.T) {\n  msg := "use t.Skip() to disable"\n  _ = msg\n}\n'
            },
            "head",
        )
        result = analyze_diff(repo, base, head, ["app_test.go"])
        # FP: t.Skip( is in a string, but counted as a skip marker
        assert result.new_skip_markers == 1

    def test_go_no_assertion_detection(self, tmp_path: Path) -> None:
        """Go has no single 'assert' keyword; t.Fatal is a call, not an assertion.
        Workproof intentionally does NOT detect t.Fatal removal for Go because
        the false-positive rate (every error-handling refactor) is too high.
        Documented here as a deliberate non-feature."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)
        base = _commit(
            repo,
            {
                "app_test.go": "package app\n\nfunc TestX(t *testing.T) {\n  if err != nil {\n    t.Fatal(err)\n  }\n}\n"
            },
            "base",
        )
        head = _commit(
            repo,
            {
                "app_test.go": "package app\n\nfunc TestX(t *testing.T) {\n  if err != nil {\n    return\n  }\n}\n"
            },
            "head",
        )
        result = analyze_diff(repo, base, head, ["app_test.go"])
        # Intentionally zero — we don't claim to catch Go assertion removal
        assert result.assertions_removed == 0


# ----- aggregation helpers -----


class TestCountsByFile:
    def test_aggregates_by_file(self) -> None:
        details = [
            {"file": "a.py", "line": 1, "content": "assert 1"},
            {"file": "a.py", "line": 5, "content": "assert 2"},
            {"file": "b.py", "line": 1, "content": "assert 3"},
        ]
        assert counts_by_file(details) == {"a.py": 2, "b.py": 1}

    def test_empty_details(self) -> None:
        assert counts_by_file([]) == {}
