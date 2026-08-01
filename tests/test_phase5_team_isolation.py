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


def test_a_new_role_template_does_not_join_the_workload_registry_by_accident() -> None:
    """Mutation: add a template under infra/iam/ declaring a `-workload` role.

    The registry check above is a glob over a directory, so it is satisfied by editing a
    tuple and cannot be satisfied by intent. This asserts the count from the other side: two
    workload roles, both in batch templates, and every other role under infra/iam/ outside
    the set -- so a third arriving is a failure here rather than three failures elsewhere.
    """
    assert len(WORKLOAD_ROLE_TEMPLATES) == 2
    assert {path for _name, path in WORKLOAD_ROLE_TEMPLATES} == {
        "infra/iam/batch-roles.yaml",
        "infra/iam/batch-gpu-roles.yaml",
    }


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


def test_both_workload_roles_reach_every_team_by_decision() -> None:
    """Mutation: narrow either one to a named team.

    That would be a tightening nobody asked for, and it has a cost that is easy to miss:
    Batch fixes a container's roles when a job definition is registered, so a role per team
    means a job definition per team. Structure built for a boundary this lab does not want.

    **The GPU role was narrow until this was written, and the history is the point.** It
    named `platform` alone so that Phase 4's cross-team criterion had something to assert --
    a role permitting every team cannot fail to reach another team's prefix. That is a
    reason about evidence rather than about risk, and a grant shaped to satisfy a check is
    the anomaly once the check turns out to encode no requirement. Asked directly, nobody
    could name a harm: one lab building one model, where another person reading your outputs
    is collaboration, and collision is already prevented by the per-run prefix segment and
    by the absence of ``s3:DeleteObject``.

    The trigger for reopening it is in the module docstring and is not the arrival of a
    second team. It is an external collaborator, a second lab, or a dataset that must not be
    trained on.
    """
    reach = {
        role.role_name: teams_reachable_by(role)  # type: ignore[arg-type]
        for role in workload_roles(PROJECT_ROOT)
    }

    assert reach[CPU_WORKLOAD] == frozenset({EVERY_TEAM})
    assert reach[GPU_WORKLOAD] == frozenset({EVERY_TEAM})


def test_the_wildcard_is_a_decision_and_the_narrow_role_is_the_one_that_needs_a_reason() -> None:
    """THIS TEST USED TO ASSERT THE OPPOSITE AND WAS WRONG.

    It refused the combination of a second bound team and any workload role holding
    ``teams/*/runs/*``, on the reasoning that binding a second team would silently widen
    the grant. Every sentence of that was accurate and the conclusion did not follow: it
    inferred an isolation requirement from the shape of the policy rather than from asking
    what the wildcard would prevent.

    Asked directly, nobody could name a harm. This is one lab building one model, where
    another person reading your outputs is collaboration rather than a threat, and
    collision is already prevented by the per-run prefix segment and the absence of
    ``s3:DeleteObject``. So the wildcard is correct and the *narrow* role is the one that
    needs justifying -- the GPU trio was scoped to one team so that Phase 4's cross-team
    criterion had something to assert, which is a reason about evidence rather than about
    risk.

    What this asserts now is that **no** workload role names a team. The asymmetry this test
    was originally written to defend is gone: the GPU trio was the last narrow grant and it
    was widened to match the CPU one, so there is no longer a role holding a shape that
    needs a reason. A named team reappearing here is a tightening somebody should have to
    argue for rather than one that arrives inside a well-intentioned change.
    """
    named_a_team = {
        role.role_name
        for role in workload_roles(PROJECT_ROOT)
        if EVERY_TEAM not in teams_reachable_by(role)  # type: ignore[arg-type]
    }

    assert named_a_team == set(), (
        f"{sorted(named_a_team)} scope a workload role to a named team; isolation between "
        "the groups sharing this account is not a goal, and a role per team costs a job "
        "definition per team because Batch fixes a container's roles at registration"
    )


def test_binding_a_team_is_a_configuration_change_and_not_an_infrastructure_one() -> None:
    """Phase 5's gate, stated as an assertion. Mutation: scope a workload role per team.

    "Adding a team is a reviewed configuration operation rather than an infrastructure
    redesign" is the gate this phase is judged on. A role per team would mean a second team
    needs an IAM template amendment, a laptop-applied deploy and a job definition -- which
    is the redesign, arriving as a well-intentioned tightening.

    So the assertion is that no workload role names a specific team, and therefore that
    binding one touches configuration only.

    **This used to carry an exception and no longer needs it.** The GPU role named a team,
    so binding a second one meant deciding whether that run path was for everybody or needed
    a role of its own -- the one place where adding a team was not purely a configuration
    change. Widening the GPU role removed the exception rather than documenting it, so what
    was "no role except this one" is now simply no role, and any number of teams may be
    bound without touching a template.
    """
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    bound = {team.team_id for team in inventory.team_bindings.teams}
    named_a_team = {
        role.role_name
        for role in workload_roles(PROJECT_ROOT)
        if EVERY_TEAM not in teams_reachable_by(role)  # type: ignore[arg-type]
    }

    assert named_a_team == set(), (
        f"{sorted(named_a_team)} would make adding a team an IAM amendment and a "
        "laptop-applied deploy rather than a line of configuration"
    )
    assert bound == set() or all(isinstance(team_id, str) for team_id in bound), (
        "team bindings are configuration and no workload role reads them, so any number "
        "may be bound without an infrastructure change"
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


def test_the_list_grant_on_the_outputs_bucket_is_scoped_by_prefix() -> None:
    """Mutation: read only resource ARNs and ignore the condition.

    ``s3:ListBucket`` is a bucket-level action, so it cannot be scoped by an object ARN at
    all -- the resource is the bucket. An unconditioned grant lets a container enumerate
    every team's output while its object grants look perfectly narrow, and a reader that
    only looked at resources would report that role as reaching one team.

    THE RULE IS ABOUT THIS BUCKET AND NOT ABOUT LISTING. It used to read every ListBucket
    statement on the role and demand a prefix of each, which was the same assertion while
    the outputs bucket was the only bucket the role could list. It stopped being the same
    assertion the moment the role was granted read on the dataset library, and the
    difference is what the condition is for: the outputs bucket is partitioned by team, so
    the prefix is the partition, and the dataset library is a published catalogue with no
    team dimension at all. The test below carries that half, so neither is left implied.
    """
    role = role_named(GPU_WORKLOAD)
    listing = [
        statement
        for policy in role.inline_policies  # type: ignore[attr-defined]
        for statement in policy.statements
        if "s3:ListBucket" in statement.action_match.actions
        and any(OUTPUTS_BUCKET in resource for resource in statement.resource_match.resources)
    ]

    assert listing, "the reader assumes a ListBucket grant on the outputs bucket exists"
    for statement in listing:
        prefixes = [
            value
            for condition in statement.conditions
            if condition.condition_key == "s3:prefix"
            for value in condition.values
        ]
        assert prefixes, f"{role.role_name} lists the outputs bucket with no prefix condition"
        assert all(prefix.startswith("teams/") for prefix in prefixes)


def test_the_dataset_library_is_readable_and_not_writable_from_the_training_role() -> None:
    """The grant that let a training run read real data, and the shape of it.

    Read-only is asymmetric on purpose. ``edullm-data`` is an airlock: a producer writes to
    a landing bucket, a validator promotes into this one, and the bucket's own policy denies
    PutObject, PutObjectTagging, AbortMultipartUpload, DeleteObject and DeleteObjectVersion
    to everything but two named roles. So a write from here would be refused anyway --
    granting only GetObject means the refusal is *also* true one layer earlier, where it can
    be read off a role diff instead of inferred from a bucket policy in another stack.

    The listing is deliberately unconditioned, which the test above would once have failed.
    A prefix condition on a published catalogue would only stop a trainer discovering which
    corpora exist, which is the first thing it has to do before reading one.
    """
    role = role_named(GPU_WORKLOAD)
    statements = [
        statement
        for policy in role.inline_policies  # type: ignore[attr-defined]
        for statement in policy.statements
        if any("edullm-data" in resource for resource in statement.resource_match.resources)
    ]

    assert statements, "the training role can no longer read the dataset library at all"
    granted = {action for statement in statements for action in statement.action_match.actions}
    assert granted == {"s3:GetObject", "s3:ListBucket"}, (
        "the training role's reach into the dataset library must stay read-only; it is a "
        f"sealed library and this role is a consumer of it, and it now holds {sorted(granted)}"
    )


def test_the_prefix_the_roles_grant_is_the_prefix_the_platform_derives() -> None:
    """Reads BOTH sides. Mutation: change output_prefix and not the templates.

    The grant and the location are two spellings of one decision, and Phase 4 inherited a
    version of this where three sources spelled it three ways and two happened to agree.

    Asserted for a team that is *not* the one every run so far declared, which is the whole
    of what widening the GPU role bought: a researcher claiming a team of their own writes
    a checkpoint the role can reach. Under the narrow grant this was the failing case, and
    it failed at write time, deep inside a training run that had already spent its GPU time.
    """
    for team in (THE_ONLY_TEAM, "evaluation"):
        derived = output_prefix(team=team, run_id="run_x")
        key = derived.removeprefix(f"s3://{OUTPUTS_BUCKET}/")

        assert key.startswith(f"teams/{team}/runs/")
        for role_name in (CPU_WORKLOAD, GPU_WORKLOAD):
            assert teams_reachable_by(role_named(role_name)) == frozenset(  # type: ignore[arg-type]
                {EVERY_TEAM}
            ), f"{role_name} cannot reach the prefix the platform derives for {team}"


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
