"""Guard against the domain layer taking on Django or I/O dependencies.

The 40%-weighted risk logic is only cheap to test exhaustively because
``tripplan.domain`` is pure Python. A single stray ``from django...`` import would
turn the whole suite into something that needs settings configured and a database
around, so the constraint is enforced mechanically rather than by convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN_ROOT = Path(__file__).resolve().parent.parent / "domain"

FORBIDDEN_IMPORT_ROOTS = {
    "django",
    "rest_framework",
    "corsheaders",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "sqlite3",
    "tripplan.adapters",
    "tripplan.services",
    "tripplan.api",
}


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def _module_id(module: Path) -> str:
    """Readable pytest id, e.g. ``domain/risk/classify.py``."""
    return str(module.relative_to(DOMAIN_ROOT.parent))


@pytest.mark.parametrize("module", _domain_modules(), ids=_module_id)
def test_domain_modules_do_not_import_framework_or_io(module: Path) -> None:
    """No Django, no HTTP client, no sockets, no database in the domain layer."""
    tree = ast.parse(module.read_text(), filename=str(module))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [alias.name for alias in node.names if _forbidden(alias.name)]
        elif isinstance(node, ast.ImportFrom) and node.module and _forbidden(node.module):
            offenders.append(node.module)

    assert not offenders, f"{module.name} imports {offenders}, which the domain layer must not do"


def _forbidden(dotted: str) -> bool:
    return any(dotted == root or dotted.startswith(f"{root}.") for root in FORBIDDEN_IMPORT_ROOTS)


def test_domain_layer_actually_has_modules_to_check() -> None:
    """Guard against the scan silently passing because a rename broke the path."""
    names = {p.name for p in _domain_modules()}
    assert {
        "enums.py",
        "thresholds.py",
        "classify.py",
        "evaluate.py",
        "miles.py",
        "recommend.py",
        "haversine.py",
        "sampling.py",
        "eta.py",
    } <= names
