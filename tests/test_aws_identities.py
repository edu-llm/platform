"""The table the mismatch filter joins through, and the four ways it can be wrong.

A role bound to a login nobody has heard of, one role bound twice, one role in both lists,
and a login spelled with the wrong case are all defects that produce a mismatch list that is
quietly short rather than an error anybody sees. All four are refused at load time here, which
is the only place they can be refused before somebody reads a report built on them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.bindings import ExcludedRole, normalize_github_login
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.mismatch import LaunchEvent, compute_mismatches, render_line

#: The role the preview submissions run under. Spelled here as a literal because the
#: exclusion in `config/organization.yaml` is a literal and `infra/iam/run-preview-role.yaml`
#: is where the name is declared: a rename on one side alone is a role that quietly stops
#: being set aside, and there is no runtime error for that.
PREVIEW_ROLE = "sbsandbox-intern-edullm-run-preview"

MINIMAL = {
    "admins": ["philote-dev"],
    "team_leads": ["philote-dev"],
    "members": [{"github_login": "philote-dev"}, {"github_login": "alsy7009"}],
    "pilot_repositories": ["OLMo-core"],
}


def _inventory(**identities: object) -> OrganizationInventory:
    return OrganizationInventory.model_validate({**MINIMAL, "aws_identities": identities})


def test_a_role_resolves_to_the_login_it_is_bound_to() -> None:
    """Mutation: have role_logins() key on the login instead of the role name."""
    inventory = _inventory(
        roles=[{"role_name": "Intern-amy.lin-sbsandbox", "github_login": "alsy7009"}],
        excluded_roles=[],
    )
    assert inventory.aws_identities.role_logins() == {"Intern-amy.lin-sbsandbox": "alsy7009"}


def test_a_role_bound_to_somebody_who_is_not_a_member_is_refused() -> None:
    """Mutation: drop the membership check from validate_inventory.

    A login that is not on the roster produces a mismatch attributed to nobody, which reads
    as a name somebody typed rather than as a table that is wrong.
    """
    with pytest.raises(ValidationError, match="must be an organization member"):
        _inventory(
            roles=[{"role_name": "Intern-nobody-sbsandbox", "github_login": "notamember"}],
            excluded_roles=[],
        )


def test_one_role_name_bound_twice_is_refused() -> None:
    """Mutation: drop the uniqueness check on role_name.

    Two bindings for one role name means the resolution depends on iteration order, so one
    person's off-platform launch lands on another person's line.
    """
    with pytest.raises(ValidationError, match="role name must be bound once"):
        _inventory(
            roles=[
                {"role_name": "Intern-amy.lin-sbsandbox", "github_login": "alsy7009"},
                {"role_name": "Intern-amy.lin-sbsandbox", "github_login": "philote-dev"},
            ],
            excluded_roles=[],
        )


def test_a_role_that_is_both_bound_and_excluded_is_refused() -> None:
    """Mutation: drop the disjointness check.

    A role in both lists is a person whose launches vanish into the exclusion, which is the
    exact failure the exclusion is written literally to avoid.
    """
    with pytest.raises(ValidationError, match="must not be both bound and excluded"):
        _inventory(
            roles=[{"role_name": "Intern-amy.lin-sbsandbox", "github_login": "alsy7009"}],
            excluded_roles=[{"role_name": "Intern-amy.lin-sbsandbox", "reason": "no"}],
        )


def test_a_login_spelled_with_the_wrong_case_still_resolves_to_the_member() -> None:
    """Mutation: compare the bound login against the roster without normalising.

    GitHub logins are case-insensitive and this roster carries `Adarsh-Rajesh-gitHub` and
    `VS-code-cloud`, so a case-sensitive membership check would refuse a table that names
    them exactly as the roster does -- or, worse, accept one that names them differently and
    then fail to join anything to them.
    """
    inventory = OrganizationInventory.model_validate(
        {
            **MINIMAL,
            "aws_identities": {
                "roles": [{"role_name": "Intern-amy.lin-sbsandbox", "github_login": "ALSY7009"}]
            },
        }
    )
    assert inventory.aws_identities.role_logins() == {"Intern-amy.lin-sbsandbox": "ALSY7009"}


def test_an_excluded_role_carries_the_reason_it_is_excluded() -> None:
    """Mutation: make ExcludedRole.reason optional.

    An exclusion with no reason is a role somebody removed from the list of things that get
    looked at, and nothing left saying why it was safe to.
    """
    with pytest.raises(ValidationError):
        ExcludedRole.model_validate({"role_name": PREVIEW_ROLE})


def test_the_committed_roster_carries_a_table_the_contract_accepts() -> None:
    """Mutation: leave aws_identities out of config/organization.yaml.

    The field defaults to an empty table so that every fixture written before it existed
    still parses, which means an empty one is valid and useless: the filter would resolve no
    role, every launch would land in the unresolved bucket, and the mismatch list would be
    permanently zero with a denominator saying why. This is what stops the default becoming
    the committed state.
    """
    inventory = load_yaml("config/organization.yaml", OrganizationInventory)
    assert inventory.aws_identities.roles, "the roster carries no AWS role bindings"
    members = {member.normalized_github_login for member in inventory.members}
    for binding in inventory.aws_identities.roles:
        assert normalize_github_login(binding.github_login) in members, binding.role_name
    # The account held 43 Intern-* roles on 2026-08-05 and twenty are the roster's. A table
    # longer than the roster is a table with something in it that is not a person.
    assert len(inventory.aws_identities.roles) <= len(inventory.members)


def test_two_people_are_never_bound_to_one_role_and_one_person_may_hold_two() -> None:
    """Mutation: make the uniqueness check cover the login instead of the role name.

    They are not symmetric. One role resolving to two people is unresolvable and the contract
    refuses it; one person holding two roles is ordinary -- an account renamed, or a name
    spelled two ways -- and refusing it would force somebody to drop one of their own roles
    out of the table to make the file load.
    """
    inventory = _inventory(
        roles=[
            {"role_name": "Intern-amy.lin-sbsandbox", "github_login": "alsy7009"},
            {"role_name": "Intern-amy.y.lin-sbsandbox", "github_login": "alsy7009"},
        ],
        excluded_roles=[],
    )
    assert len(inventory.aws_identities.role_logins()) == 2


# --------------------------------------------------------------------------------------
# The preview role, set aside by name and counted
# --------------------------------------------------------------------------------------


def test_the_preview_role_is_excluded_by_its_literal_name() -> None:
    """Mutation: exclude on a prefix such as `sbsandbox-intern-edullm-` instead.

    A prefix would also swallow the deployer, the image resolver, the run canceller and the
    audit reader, and it would swallow the next role added under that name with nothing
    anywhere recording that it stopped being examined. `infra/iam/run-preview-role.yaml` is
    what declares the role; this is the only place its launches are set aside.
    """
    inventory = load_yaml("config/organization.yaml", OrganizationInventory)
    assert PREVIEW_ROLE in inventory.aws_identities.excluded_role_names()
    for excluded in inventory.aws_identities.excluded_roles:
        assert not excluded.role_name.endswith("*")
        assert not excluded.role_name.endswith("-")


def test_the_excluded_role_is_the_one_the_template_declares() -> None:
    """Mutation: rename the role in the template and leave the exclusion behind.

    Nothing at runtime notices. The renamed role stops being excluded, its preview launches
    become mismatches, and the morning message fills with the owner testing the platform --
    which is the failure the exclusion exists to prevent, arriving as a name mismatch nobody
    is looking for. Read out of the template rather than compared to a second literal.
    """
    import yaml

    from tests.infrastructure_support import IAM_ROOT

    template = yaml.safe_load((IAM_ROOT / "run-preview-role.yaml").read_text(encoding="utf-8"))
    declared = {
        properties["Properties"]["RoleName"]
        for properties in template["Resources"].values()
        if properties.get("Type") == "AWS::IAM::Role"
    }
    inventory = load_yaml("config/organization.yaml", OrganizationInventory)

    assert declared == {PREVIEW_ROLE}
    assert declared <= set(inventory.aws_identities.excluded_role_names())


def test_an_excluded_role_is_counted_rather_than_vanishing() -> None:
    """Mutation: skip an excluded event instead of tallying it.

    A preview launch that leaves no trace is the same failure as an unresolved role that
    leaves no trace: the reader cannot tell an exclusion that fired forty times from one that
    is not wired up at all.
    """
    event = LaunchEvent(
        event_id="e1",
        event_name="SubmitJob",
        occurred_at=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
        role_name=PREVIEW_ROLE,
        run_id=None,
    )
    report = compute_mismatches(
        [event],
        role_logins={},
        excluded_roles=(PREVIEW_ROLE,),
        known_run_ids=frozenset(),
    )
    assert report.mismatches == ()
    assert report.unresolved == ()
    assert report.excluded_launches == 1
    assert PREVIEW_ROLE in render_line(report)


def test_the_exclusion_is_reported_on_a_day_it_matched_nothing() -> None:
    """Mutation: build the excluded tally from the events rather than from the list.

    An exclusion that only appears on days it fired is invisible on every other day, and an
    invisible exclusion is one nobody re-judges when the role it names is retired.
    """
    report = compute_mismatches(
        [], role_logins={}, excluded_roles=(PREVIEW_ROLE,), known_run_ids=frozenset()
    )
    assert [(t.role_name, t.launches) for t in report.excluded] == [(PREVIEW_ROLE, 0)]
    assert PREVIEW_ROLE in render_line(report)
