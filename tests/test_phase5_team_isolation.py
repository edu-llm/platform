"""Which teams each workload role can reach, and the one that can reach all of them.

Phase 5's isolation claim rests on the grants being written in terms of teams. These read
that from the committed templates and hold it to being true of *both* workload roles, which
is how the difference between them surfaced: the GPU role names one team and the CPU role
names a wildcard, and nothing anywhere said so.

The wildcard is asserted rather than fixed. It is a deployed grant, and narrowing it is a
laptop-applied IAM change that drifts a committed Phase 3 capture -- so it is recorded here
as the measured state, with a test that fails the moment somebody adds a second team without
dealing with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix
from edullm_platform.role_drift import load_template_roles
from edullm_platform.team_isolation import (
    EVERY_TEAM,
    WORKLOAD_ROLE_TEMPLATES,
    reach_of,
    teams_reachable_by,
    workload_roles,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The one team that exists in practice: every committed run declared it, and the GPU role
#: is written for it. Not read from the roster, because the roster has no team bindings --
#: which is itself the finding the last test in this file records.
THE_ONLY_TEAM = "platform"

CPU_WORKLOAD = "sbsandbox-intern-edullm-batch-workload"
GPU_WORKLOAD = "sbsandbox-intern-edullm-batch-gpu-workload"


def role_named(name: str) -> object:
    return next(role for role in workload_roles(PROJECT_ROOT) if role.role_name == name)


def test_every_registered_workload_role_is_declared_where_the_registry_says() -> None:
    """Mutation: move a role to a different template and leave the registry alone.

    The registry is what every check here iterates, so a role it cannot find is a role
    nothing examines -- and the failure is silence rather than an error, because a check
    over an empty set passes.
    """
    roles = workload_roles(PROJECT_ROOT)

    assert len(roles) == len(WORKLOAD_ROLE_TEMPLATES)
    assert {role.role_name for role in roles} == {CPU_WORKLOAD, GPU_WORKLOAD}


def test_the_registry_holds_every_role_a_container_actually_runs_as() -> None:
    """Reads BOTH sides. Mutation: add a third workload role and not register it.

    A workload role the registry does not know about is a container whose reach nothing
    checks. Found by name rather than by listing the registry, because the point is to
    catch the role that exists in a template and not in the tuple.
    """
    declared = {
        role.role_name
        for path in sorted(Path(PROJECT_ROOT / "infra" / "iam").glob("*.yaml"))
        for role in load_template_roles(path)
        if role.role_name.endswith("-workload")
    }

    assert declared == {name for name, _path in WORKLOAD_ROLE_TEMPLATES}


def test_no_workload_role_reaches_outside_the_team_prefix_shape() -> None:
    """Mutation: grant a workload role the whole outputs bucket.

    The team segment is what makes cross-team isolation expressible at all. A grant on
    ``…-outputs/*`` reads as narrow -- it is one bucket, and the bucket is the platform's --
    while permitting every team's output, and this reader would report it as reaching no
    teams rather than all of them, which is the wrong direction to be wrong in.
    """
    for role in workload_roles(PROJECT_ROOT):
        reached = teams_reachable_by(role)  # type: ignore[arg-type]
        assert reached, (
            f"{role.role_name} grants nothing under teams/{{team}}/runs/, so either it "
            "reaches the bucket some other way or this reader has stopped seeing it"
        )
        for statement_resource in (
            f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}/*",
            f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}/teams/*",
        ):
            for policy in role.inline_policies:  # type: ignore[attr-defined]
                for statement in policy.statements:
                    assert statement_resource not in statement.resource_match.resources, (
                        f"{role.role_name} reaches {statement_resource}, which is every "
                        "team's output written so that it does not look like it"
                    )


def test_the_gpu_workload_role_names_exactly_one_team() -> None:
    """Mutation: widen it to teams/*/runs/* to match the CPU role.

    It was written narrow on purpose, and the narrowness is what makes Phase 4's cross-team
    criterion closeable at all -- a role permitting every team cannot fail to reach another
    team's prefix, so the check would have nothing to assert.
    """
    reached = teams_reachable_by(role_named(GPU_WORKLOAD))  # type: ignore[arg-type]

    assert reached == frozenset({THE_ONLY_TEAM})
    assert EVERY_TEAM not in reached


def test_the_cpu_workload_role_reaches_every_team_and_this_is_the_phase_five_gap() -> None:
    """The measured state, asserted so that fixing it has to change this test.

    Mutation: none -- this records a defect rather than guarding against one. The CPU
    workload role is scoped ``teams/*/runs/*``, which is every team that will ever exist.
    Today the wildcard has exactly one thing to match, so nothing is exposed and nothing
    fails.

    What makes it worth a test rather than a comment is how it would arrive: binding a
    second team widens this grant by doing nothing to it. No run fails, no deploy happens,
    no diff shows anything -- the pattern simply starts matching a name it did not match
    yesterday. That is the failure mode a test can catch and a review cannot.
    """
    reached = teams_reachable_by(role_named(CPU_WORKLOAD))  # type: ignore[arg-type]

    assert reached == frozenset({EVERY_TEAM})


def test_binding_a_second_team_is_refused_until_the_wildcard_is_dealt_with() -> None:
    """The guard the gap above needs. Mutation: drop it and bind a team.

    This is the test that turns a recorded defect into a blocked one. While one team exists
    the wildcard is harmless; the moment ``organization.yaml`` binds a second, the CPU role
    permits each of them to read and write the other's output, and nothing else in this
    repository would notice.

    Written against the roster rather than against a constant, so it fires on the change
    that matters -- adding a binding -- rather than on somebody remembering to update a
    number.
    """
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    bound = {team.team_id for team in inventory.team_bindings.teams}
    wide = {
        role.role_name
        for role in workload_roles(PROJECT_ROOT)
        if EVERY_TEAM in teams_reachable_by(role)  # type: ignore[arg-type]
    }

    assert len(bound) <= 1 or not wide, (
        f"{sorted(bound)} teams are bound and {sorted(wide)} can reach every team's "
        "output. Scope the workload role per team before a second team is bound, or the "
        "isolation this phase claims is a wildcard that happens to match one name."
    )


def test_read_and_write_are_recorded_separately_because_they_fail_differently() -> None:
    """Mutation: collapse the reach to a set of team names and drop the actions.

    A role that can read another team's outputs leaks research; one that can write there
    corrupts it; one that can only list learns what exists. Those need different answers,
    and a check that had only the team names could not tell them apart.
    """
    reaches = reach_of(role_named(GPU_WORKLOAD))  # type: ignore[arg-type]

    assert len(reaches) == 1
    assert reaches[0].actions >= {"s3:PutObject", "s3:GetObject", "s3:ListBucket"}
    assert "s3:DeleteObject" not in reaches[0].actions, (
        "delete is deliberately absent: every run writes under its own run id, so a role "
        "that needed delete would be a signal that two runs share a prefix"
    )


def test_the_list_grant_is_scoped_by_prefix_and_not_only_by_bucket() -> None:
    """Mutation: read only resource ARNs and ignore the condition.

    ``s3:ListBucket`` is a bucket-level action, so it cannot be scoped by an object ARN at
    all -- the resource is the bucket. An unconditioned grant lets a container enumerate
    every team's output while its object grants look perfectly narrow, and a reader that
    only looked at resources would report that role as reaching one team.
    """
    role = role_named(GPU_WORKLOAD)
    listing = [
        statement
        for policy in role.inline_policies  # type: ignore[attr-defined]
        for statement in policy.statements
        if "s3:ListBucket" in statement.action_match.actions
    ]

    assert listing, "the reader assumes a ListBucket grant exists to be scoped"
    for statement in listing:
        prefixes = [
            value
            for condition in statement.conditions
            if condition.condition_key == "s3:prefix"
            for value in condition.values
        ]
        assert prefixes, f"{role.role_name} lists the bucket with no prefix condition"
        assert all(prefix.startswith("teams/") for prefix in prefixes)


def test_the_prefix_the_roles_grant_is_the_prefix_the_platform_derives() -> None:
    """Reads BOTH sides. Mutation: change output_prefix and not the templates.

    The grant and the location are two spellings of one decision, and Phase 4 inherited a
    version of this where three sources spelled it three ways and two happened to agree.
    """
    derived = output_prefix(team=THE_ONLY_TEAM, run_id="run_x")
    key = derived.removeprefix(f"s3://{OUTPUTS_BUCKET}/")

    assert key.startswith(f"teams/{THE_ONLY_TEAM}/runs/")
    assert teams_reachable_by(role_named(GPU_WORKLOAD)) == frozenset(  # type: ignore[arg-type]
        {key.split("/")[1]}
    )


@pytest.mark.parametrize("role_name", [CPU_WORKLOAD, GPU_WORKLOAD])
def test_no_workload_role_can_reach_the_store_that_records_what_it_did(role_name: str) -> None:
    """Mutation: grant a workload role anything on the lineage bucket.

    The property the whole write-once design rests on. A container that can write to the
    lineage store can rewrite the record of what it did, and every other guarantee here is
    downstream of that record being something only the platform writes.
    """
    role = role_named(role_name)
    reached = [
        resource
        for policy in role.inline_policies  # type: ignore[attr-defined]
        for statement in policy.statements
        for resource in statement.resource_match.resources
        if "lineage" in resource
    ]

    assert reached == []
