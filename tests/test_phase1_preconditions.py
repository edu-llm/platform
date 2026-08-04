from pathlib import Path

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory, normalize_github_login

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODEOWNERS_PATH = PROJECT_ROOT / ".github" / "CODEOWNERS"


def _read_required(path: Path) -> str:
    assert path.is_file(), f"required file is missing: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def _codeowners_rules() -> dict[str, tuple[str, ...]]:
    rules: dict[str, tuple[str, ...]] = {}
    for line in _read_required(CODEOWNERS_PATH).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pattern, *owners = stripped.split()
        rules[pattern] = tuple(owners)
    return rules


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


def test_config_is_owned_by_the_admins_and_exactly_the_recorded_team_leads() -> None:
    """The wider ownership of ``/config/**`` is held to the roster it was derived from.

    ``/config/**`` names nine logins rather than the two every other owned path names, and
    the argument for that is in the CODEOWNERS header: config is where the volume is and it
    is the one owned tree whose mistakes this suite catches before a reviewer would. That
    argument only holds while the list is the leads.

    The mutation this catches is the quiet one. ``config/organization.yaml`` records who
    leads a group and CODEOWNERS records who may approve a change to it; they are two files
    and nothing joins them, so a lead added or replaced in the roster leaves this line
    describing the previous set -- somebody who no longer leads anything approving config,
    or a new lead still queueing behind the two admins. That is the same shape of drift the
    roster's own header records against the GitHub ``team-leads`` team, which went two days
    out of step and which nothing in this repository could notice. This is the notice.
    """
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    expected = {normalize_github_login(login) for login in inventory.admins} | {
        normalize_github_login(login) for login in inventory.team_leads
    }

    owners = _codeowners_rules().get("/config/**")
    assert owners is not None, "/config/** has no CODEOWNERS rule"
    assert all(owner.startswith("@") for owner in owners)
    # A team would satisfy a set comparison against the leads today and stop meaning the
    # leads the moment somebody is added to it in the organization settings, which is a
    # change no commit here would show. Individual logins only.
    assert not any("/" in owner for owner in owners), (
        f"/config/** names a GitHub team: {owners}. Membership of a team is edited in the "
        "organization settings and does not appear in this repository, so ownership of "
        "config would follow a change nobody here could review."
    )
    assert {normalize_github_login(owner.removeprefix("@")) for owner in owners} == expected
