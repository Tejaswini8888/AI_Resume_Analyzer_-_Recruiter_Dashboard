"""Import validation for CI (Step 3, requirement 7).

A Streamlit page only executes when a user navigates to it, so a broken import
- a renamed symbol, a bad relative path, a dependency that is not in
``requirements.txt`` - sits undetected until someone clicks. On a fresh CI
machine that is exactly the failure mode this step exists to catch, because the
dependency set there is built from the requirement files rather than from
whatever happens to be installed on a developer's machine.

Three checks, all of which must pass:

1. **Every module in** ``core/``, ``modules/``, ``services/`` and ``ui/``
   imports for real. These packages are side-effect free apart from building
   settings, so importing them is safe and catches missing dependencies.
2. **Every Streamlit page byte-compiles.** A page cannot be *imported* - its
   top level calls ``st.*`` - so compilation is the strongest check available
   without a running Streamlit runtime. It fully parses the file, so a syntax
   error, a removed import or an unparseable construct all fail here.
3. **Every project-level import in** ``app.py`` **and every page resolves.**
   Parsed with ``ast`` and imported by module path, so a page that does
   ``from core.foo import Bar`` fails now rather than on click. This validates
   ``app.py``'s dependency surface without executing its UI code.

Every failure is collected and reported together. Aborting on the first
``ImportError`` would hide the other nineteen.

Run it exactly as CI does::

    python tools/ci_import_check.py

Exit code 0 = all imports resolve, 1 = at least one failure (listed on stdout).
"""

import ast
import importlib
import py_compile
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Running this file as a script puts ``tools/`` on sys.path, not the project
# root, so first-party packages are unimportable without this. pytest hides the
# problem because pytest.ini sets ``pythonpath = .``; CI runs this script
# directly, so the bootstrap has to live here.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Packages whose modules are safe to import in a bare interpreter.
IMPORTABLE_PACKAGES = ("core", "modules", "services", "ui")

# Top-level names that are first-party. Anything else in an import statement is
# a third-party package and is validated implicitly by importing this script.
FIRST_PARTY_ROOTS = set(IMPORTABLE_PACKAGES)


def _iter_importable_modules():
    """Yield dotted module names for every non-``__init__`` file in the packages."""
    for package in IMPORTABLE_PACKAGES:
        for path in sorted((PROJECT_ROOT / package).rglob("*.py")):
            if path.name == "__init__.py":
                continue
            relative = path.relative_to(PROJECT_ROOT).with_suffix("")
            yield ".".join(relative.parts)


def _iter_pages():
    return sorted((PROJECT_ROOT / "pages").glob("*.py"))


def _project_imports(path):
    """First-party ``from <project>... import X`` statements in a file.

    Yields ``(module_name, symbol_name_or_None)`` pairs. ``import <project>...``
    forms yield a ``None`` symbol. Relative imports (``level > 0``) are skipped:
    their meaning depends on package layout rather than on a module name.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            if node.module.split(".")[0] in FIRST_PARTY_ROOTS:
                for alias in node.names:
                    if alias.name == "*":
                        yield node.module, None
                    else:
                        yield node.module, alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FIRST_PARTY_ROOTS:
                    yield alias.name, None


def _import_target(module_name, symbol):
    """Import ``module_name``; return the object a symbol should live on.

    ``from services import auth_service`` imports a *submodule*, and the parent
    package only grows an attribute for it once the submodule has been imported.
    A naive ``hasattr(services, "auth_service")`` therefore reports a false
    failure for a completely valid import. Resolve the submodule when the symbol
    is not an attribute of the module itself.
    """
    module = importlib.import_module(module_name)
    if symbol is None or hasattr(module, symbol):
        return module, True
    try:
        return importlib.import_module(f"{module_name}.{symbol}"), True
    except Exception:
        return module, False


def main():
    failures = []
    checked = 0

    # 1. Real imports ---------------------------------------------------------
    for module_name in _iter_importable_modules():
        checked += 1
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(
                f"IMPORT  {module_name}: {type(exc).__name__}: {exc}"
            )

    # 2. Pages must at least compile -----------------------------------------
    with tempfile.TemporaryDirectory(prefix="talentiq_ci_compile_") as tmp:
        tmp_dir = Path(tmp)
        for page in _iter_pages():
            checked += 1
            try:
                py_compile.compile(
                    str(page),
                    doraise=True,
                    cfile=str(tmp_dir / f"{page.stem}.pyc"),
                )
            except py_compile.PyCompileError as exc:
                failures.append(f"COMPILE {page.name}: {exc}")

        # 3. app.py's own first-party imports must resolve, and every symbol
        #    it names must exist on the module it comes from. This validates
        #    app.py's dependency surface without executing its Streamlit code.
        for entry in (PROJECT_ROOT / "app.py", *_iter_pages()):
            checked += 1
            for module_name, symbol in _project_imports(entry):
                try:
                    target, symbol_ok = _import_target(module_name, symbol)
                except Exception as exc:
                    failures.append(
                        f"RESOLVE {entry.name} -> {module_name}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                if symbol and not symbol_ok:
                    failures.append(
                        f"SYMBOL  {entry.name} imports {symbol!r} from "
                        f"{module_name}, which defines neither that name nor a "
                        f"{module_name}.{symbol} submodule"
                    )

    if failures:
        print("FAIL: import validation found problems:\n", file=sys.stderr)
        for problem in failures:
            print(f"  - {problem}", file=sys.stderr)
        print(
            f"\n{len(failures)} problem(s) across {checked} check(s).",
            file=sys.stderr,
        )
        return 1

    counts = {
        "modules": sum(1 for _ in _iter_importable_modules()),
        "pages": len(_iter_pages()),
    }
    print(
        f"OK: {counts['modules']} modules imported, "
        f"{counts['pages']} pages compiled, "
        f"app.py + page imports resolved ({checked} checks)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
