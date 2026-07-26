from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODEOWNERS_PATH = PROJECT_ROOT / ".github" / "CODEOWNERS"


def _read_required(path: Path) -> str:
    assert path.is_file(), f"required file is missing: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_codeowners_protects_phase1_infrastructure_surfaces() -> None:
    lines = {
        line.strip()
        for line in _read_required(CODEOWNERS_PATH).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    expected_owners = "@philote-dev @BritishAmericqn"
    assert f"/.github/CODEOWNERS {expected_owners}" in lines
    assert f"/.github/workflows/** {expected_owners}" in lines
    assert f"/infra/** {expected_owners}" in lines
