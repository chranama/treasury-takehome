import ast
from pathlib import Path

COMPARISON_ROOT = Path(__file__).resolve().parents[2] / "app" / "comparison"
FORBIDDEN_IMPORT_ROOTS = {"fastapi", "frontend", "openai", "sqlite3"}


def test_comparison_domain_has_no_infrastructure_imports() -> None:
    violations: list[str] = []
    for path in sorted(COMPARISON_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_roots: list[str] = []
            if isinstance(node, ast.Import):
                imported_roots = [alias.name.partition(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots = [node.module.partition(".")[0]]
            for imported_root in imported_roots:
                if imported_root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path.name}:{node.lineno} imports {imported_root}")

    assert not violations, violations
