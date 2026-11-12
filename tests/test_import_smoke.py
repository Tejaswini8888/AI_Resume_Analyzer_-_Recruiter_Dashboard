"""Import smoke tests (Phase 4 · Step 6 audit).

A Streamlit page is only executed when a user navigates to it, so a broken
import (a removed symbol, a bad relative path, a missing dependency) sits
undetected until someone clicks the page. That is exactly how a deleted
``SCORE_ELIGIBLE_MIN`` constant shipped: the whole test session failed to
collect only because two test modules imported it too.

Two complementary checks:

1. ``test_every_module_imports`` imports every non-page module for real. These
   are safe to import (no top-level side effects beyond building settings).
2. ``test_every_page_compiles`` compiles every page without executing it, since
   a page's top-level code renders Streamlit UI on import.

Together they guarantee every page and module is at least loadable.
"""

import importlib
import py_compile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Modules that are not importable in a test process.
_EXCLUDED_MODULES = {"__main__"}


def _iter_module_names():
    for package in ("core", "modules", "services", "ui"):
        for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
            if path.name == "__init__.py":
                continue
            relative = path.relative_to(PROJECT_ROOT).with_suffix("")
            yield ".".join(relative.parts)


def _iter_page_paths():
    return sorted((PROJECT_ROOT / "pages").glob("*.py"))


def _iter_app_entrypoints():
    return [PROJECT_ROOT / "app.py", *sorted((PROJECT_ROOT / "ui").glob("*.py"))]


def test_module_discovery_finds_the_expected_tree():
    """Guards the discovery helpers themselves: if a rename moved packages,
    the tests below would silently pass by checking nothing."""
    names = set(_iter_module_names())
    assert "core.db" in names
    assert "core.models" in names
    assert "modules.jd_matcher" in names
    assert "services.analysis_service" in names
    assert "ui.theme" in names
    assert _iter_page_paths(), "no Streamlit pages found"


@pytest.mark.parametrize("module_name", sorted(set(_iter_module_names())))
def test_every_module_imports(module_name):
    if module_name in _EXCLUDED_MODULES:
        pytest.skip("not importable")
    importlib.import_module(module_name)


@pytest.mark.parametrize(
    "page", _iter_page_paths(), ids=lambda p: p.stem
)
def test_every_page_compiles(page, tmp_path):
    """Byte-compile a page, which fully parses it, without running its UI."""
    py_compile.compile(
        str(page),
        cfile=str(tmp_path / f"{page.stem}.pyc"),
        doraise=True,
    )


@pytest.mark.parametrize(
    "entrypoint", _iter_app_entrypoints(), ids=lambda p: p.stem
)
def test_app_and_ui_entrypoints_compile(entrypoint, tmp_path):
    py_compile.compile(
        str(entrypoint),
        cfile=str(tmp_path / f"{entrypoint.stem}.pyc"),
        doraise=True,
    )


def test_pages_only_reference_existing_project_modules():
    """A page importing a symbol that no longer exists is a runtime crash on
    navigation. Every ``from <project>... import X`` in a page must resolve."""
    import ast
    import importlib

    problems = []
    for page in _iter_page_paths():
        tree = ast.parse(page.read_text(encoding="utf-8"), filename=str(page))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level != 0 or not node.module:
                continue
            root = node.module.split(".")[0]
            if root not in {"core", "modules", "services", "ui"}:
                continue  # third-party
            try:
                target = importlib.import_module(node.module)
            except Exception as exc:  # pragma: no cover - reported below
                problems.append(f"{page.name}: cannot import {node.module} ({exc})")
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if not hasattr(target, alias.name):
                    problems.append(
                        f"{page.name}: {node.module} has no attribute "
                        f"{alias.name!r} (imported at line {node.lineno})"
                    )
    assert not problems, "unresolvable page imports:\n" + "\n".join(problems)
