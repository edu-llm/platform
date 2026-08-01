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
from typing import Final

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
    is collaboration, and collision is already prevented by the per-run prefix segment.

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
    collision is already prevented by the per-run prefix segment. So the wildcard is correct
    and the *narrow* role is the one that needs justifying -- the GPU trio was scoped to one
    team so that Phase 4's cross-team criterion had something to assert, which is a reason
    about evidence rather than about risk.

    This used to cite the absence of ``s3:DeleteObject`` as a second reason and no longer
    can: the role holds one, scoped to ``checkpoints/*``, so that a retry can rewrite a step
    directory its own lost attempt tore. The prefix segment is what the argument rested on
    anyway, and the delete's own bounds are asserted below rather than folded in here.

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


#: The delete's exact scope, spelled once so the two tests below compare against the same
#: string rather than against each other's idea of it. Anything wider is a different grant.
CHECKPOINT_DELETE_SCOPE: Final = (
    f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}/teams/*/runs/*/checkpoints/*"
)

#: The one key under a step directory the delete may not reach. ``.metadata.json`` is written
#: last and ``Checkpointer.dir_is_checkpoint`` requires it, so it is what makes a directory a
#: checkpoint rather than the remains of a write.
FINISHED_CHECKPOINT_MARKER: Final = f"{CHECKPOINT_DELETE_SCOPE}/.metadata.json"


def statements_of(role_name: str) -> list[object]:
    role = role_named(role_name)
    return [
        statement
        for policy in role.inline_policies  # type: ignore[attr-defined]
        for statement in policy.statements
    ]


def test_the_delete_reaches_checkpoints_and_the_exact_arn_is_the_assertion() -> None:
    """Mutation: widen the delete to ``teams/*/runs/*``, or to the whole outputs bucket.

    THIS FILE FORBADE THE DELETE OUTRIGHT AND THE REASONING WAS HALF RIGHT. It read: every
    run writes under its own run id, so no previous run's object is ever in the way, and a
    role that needed delete would be a signal that two runs share a prefix, which is the
    condition to fail on rather than to permit. Every clause of that is about *two* runs.

    The case it does not cover is one run twice. A run's own retry is in its own way. On
    ``run_019fbe1f-b84f-703a-8eb8-2b4504232948`` the host was lost immediately after
    ``checkpoints/step100/train/rank0.pt`` was written, leaving that one object and no shard.
    Attempt 2 resumed from ``step50`` correctly, trained back to step 100, and died in
    ``Checkpointer._prepare_dir`` with ``FileExistsError`` on a directory that is not empty.
    Same run id, same prefix, no second run anywhere, and deterministic: every further
    attempt reaches the same step and dies at it.

    So the assertion is the scope rather than the absence, and it is written as an equality
    on the resource tuple. A widening is what this is for. ``teams/*/runs/*`` would let a
    training container delete a run's saved config, its logs and anything else the platform
    ever writes under a run; the whole bucket would let it delete another team's results.
    Neither is needed to rewrite a step directory, so neither should pass a test.
    """
    granting = [
        statement
        for statement in statements_of(GPU_WORKLOAD)
        if statement.effect == "Allow"  # type: ignore[attr-defined]
        and any(
            action.startswith("s3:Delete")
            for action in statement.action_match.actions  # type: ignore[attr-defined]
        )
    ]

    assert len(granting) == 1, (
        f"expected one Allow granting a delete on {GPU_WORKLOAD}; found {len(granting)}, "
        "and a second one is how a scope gets widened without the first one changing"
    )
    assert granting[0].action_match.actions == ("s3:DeleteObject",), (  # type: ignore[attr-defined]
        "s3:DeleteObject alone. s3:DeleteObjectVersion is what would turn a delete marker "
        "on a versioned bucket into the removal of bytes, and this role must not hold it"
    )
    assert granting[0].resource_match.resources == (CHECKPOINT_DELETE_SCOPE,), (  # type: ignore[attr-defined]
        "the delete must name the checkpoints prefix exactly. It is narrower than the "
        "s3:PutObject grant on teams/*/runs/* deliberately: clearing an unfinished step "
        "directory is the only thing this role deletes for"
    )


def test_nothing_may_delete_the_object_that_makes_a_checkpoint_finished() -> None:
    """Mutation: drop the Deny, on the ground that the Allow above is already narrow.

    The Allow reaches every key under a step directory, which includes the step directories
    of finished checkpoints. What keeps a finished one safe is that ``.metadata.json`` is
    written last and ``dir_is_checkpoint`` requires it, so a directory holding one is
    complete and a torn directory never holds one. Denying that single key by name means the
    repair path -- which only ever clears directories the loader refuses -- is unaffected,
    while the routine deletion of a finished checkpoint is refused at the policy rather than
    by a caller remembering not to.

    That routine deletion has a name and a default. ``CheckpointerCallback.max_checkpoints``
    is 3, and its prune removes ``.metadata.json`` first, on purpose, to invalidate the
    checkpoint before clearing the rest. So the prune still fails on its first call and the
    run still stops rather than losing a checkpoint, which is the behaviour
    ``GETTING-STARTED.md`` documents and which the Allow alone would have removed.
    """
    denying = [
        statement
        for statement in statements_of(GPU_WORKLOAD)
        if statement.effect == "Deny"  # type: ignore[attr-defined]
    ]

    assert len(denying) == 1
    assert denying[0].action_match.actions == ("s3:DeleteObject",)  # type: ignore[attr-defined]
    assert denying[0].resource_match.resources == (FINISHED_CHECKPOINT_MARKER,), (  # type: ignore[attr-defined]
        "the deny must name .metadata.json under a step directory. Any other spelling "
        "either misses the key the prune removes first or reaches keys the repair needs"
    )
    assert denying[0].conditions == (), (  # type: ignore[attr-defined]
        "unconditional. A condition would make the refusal depend on how the call was "
        "made, and the point is that this key is not deletable by this role at all"
    )


def test_neither_workload_role_may_remove_a_version() -> None:
    """Mutation: add ``s3:DeleteObjectVersion`` beside the delete.

    The outputs bucket has versioning enabled, so every delete either role can make writes
    a delete marker and leaves the versions under it in place. That is the whole reason the
    grant above is defensible: it cannot destroy bytes, only hide them, and a checkpoint
    deleted by mistake is recoverable by removing the marker.

    ``s3:DeleteObjectVersion`` is what removes the version itself, and
    ``s3:PutBucketVersioning`` is what would let a container turn versioning off and make
    every later delete permanent. Neither is granted, and this is the assertion that keeps
    the sentence above true rather than true as of today.
    """
    for role in workload_roles(PROJECT_ROOT):
        granted = {
            action
            for policy in role.inline_policies
            for statement in policy.statements
            if statement.effect == "Allow"
            for action in statement.action_match.actions
        }

        assert "s3:DeleteObjectVersion" not in granted, (
            f"{role.role_name} could remove a version, so a mistaken delete would stop "
            "being recoverable and the delete grant would stop being reversible"
        )
        assert "s3:PutBucketVersioning" not in granted, (
            f"{role.role_name} could turn versioning off, after which every delete it "
            "makes is permanent"
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
