"""The research groups this platform recognizes, and the GitHub teams they are.

A run has declared a ``team`` since Phase 0 and nothing has ever been able to say whether
the value was a real group. The form takes free text, its own help says a typo delays
nothing, and the only GitHub teams in the organization were ``team-leads`` and
``team-members``, neither of which is a research group. So the string travelled the whole
length of the system, into the manifest, into the immutable decision record, into the S3
prefix and onto the Batch job as ``edullm:team``, and the first reader able to notice it was
wrong was a person reading a cost report.

These hold the two halves of closing that. ``config/organization.yaml`` now declares the
groups, and each declaration names the GitHub team it is; the tests below check the
declaration against the platform's own run records on one side and against a capture of the
live organization on the other.

**Both directions, as two tests rather than one, because they are different incidents with
different fixes.** A declared slug with no GitHub team behind it is a group whose members
can never be added to anything, and the fix is to create the team. A GitHub team with no
declaration is access somebody granted that no run can be attributed against, and the fix
is either a binding or deleting the team. A single test asserting set equality would report
both as one failure and name neither.

**What is deliberately not asserted is membership.** Every group is empty, in the roster and
on GitHub, because which group a person belongs to is the one fact nothing here has ever
recorded. That state is pinned rather than passed over: the first person bound to a group
should be a reviewed edit, not something that turns up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix
from edullm_platform.evidence import StaleEvidenceError
from edullm_platform.phase2_evidence import ROLE_TEAM_SLUGS, ResearchTeamInventory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "fixtures"
CAPTURE = FIXTURES / "evidence" / "phase-2" / "github" / "research-teams.sanitized.json"


def inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


#: The three group names this platform renamed on 2026-08-01, listed here because a forward
#: rename leaves them behind rather than rewriting them. ``config/organization.yaml`` carries
#: the table and the argument; this is the machine-readable half, and it is written down twice
#: deliberately: the file is what a person reads and this is what the assertions below use, so
#: a name dropped from one is caught by the test that pins the pair together.
RETIRED_TEAM_IDS: frozenset[str] = frozenset({"tokenizer", "modeling", "curriculum"})


def declared_team_ids() -> set[str]:
    return {team.team_id for team in inventory().team_bindings.teams}


def captured_teams() -> ResearchTeamInventory:
    """The live organization as somebody recorded it, or a skip saying it has expired.

    A capture is a statement about how GitHub is configured now, so it expires, and an
    expired one must not read as current. Skipping rather than failing is the treatment the
    other GitHub captures get: a record going stale is somebody needing to re-run the
    capture, not the repository developing a defect.
    """
    document = json.loads(CAPTURE.read_text(encoding="utf-8"))
    try:
        return ResearchTeamInventory.model_validate(document)
    except (StaleEvidenceError, ValueError) as error:
        if "stale" not in str(error) and "older than" not in str(error):
            raise
        pytest.skip(f"{CAPTURE.name} has aged out of the freshness window: {error}")


def teams_named_by_committed_runs() -> set[str]:
    """Every team any committed record or manifest fixture declares.

    Read from the records rather than listed here, because a list would be the same claim
    made twice and the second copy is the one that goes stale.
    """
    named: set[str] = set()
    for path in sorted((FIXTURES / "manifests").glob("*.yaml")):
        named.add(load_yaml(path, RunManifest).team)
    for path in sorted((FIXTURES / "evidence").rglob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, str):
            document = json.loads(document)
        if not isinstance(document, dict):
            continue
        try:
            named.add(IntentRecord.model_validate(document).manifest.team)
        except ValueError:
            continue
    return named


def test_every_team_a_committed_run_declared_is_declared_or_deliberately_retired() -> None:
    """Mutation: submit under a group name nobody wrote down, and declare only the rest.

    This is the direction that matters for attribution. A run whose team is neither a declared
    group nor a name this file knows was retired is spend and output filed under a name the
    platform does not recognize, and because the manifest is hashed and the decision record
    immutable, it stays filed there.

    THE RETIRED NAMES ARE PERMITTED HERE AND NOWHERE ELSE, and the allowance is what makes the
    rename a forward one. ``tokenizer``, ``modeling`` and ``curriculum`` are gone from the
    roster and from the submission form, so nothing new can arrive under them; what is left is
    six committed fixtures that still name two of them. Those were not rewritten, for the same
    reason a lineage record is not: their canonical bytes are digest-pinned by
    ``proof/phase-0/serialization-goldens.json``, so editing one breaks a recorded digest.

    The allowance is bounded rather than open. It is exactly three names, asserted below, and
    it does not extend to the form, which is held to the declared ids alone by
    ``tests/test_submission_form_options.py``. A fourth rename has to be added here by hand,
    which is the point: retiring a name is a decision and this is where it is written down.
    """
    named = teams_named_by_committed_runs()
    resolvable = declared_team_ids() | set(RETIRED_TEAM_IDS)

    assert named, "no committed manifest or record names a team, so this test checks nothing"
    assert named <= resolvable, (
        f"{sorted(named - resolvable)} appear as the team on a committed run and are neither "
        "declared by a group in config/organization.yaml nor listed there as retired, so "
        "nothing can attribute them"
    )
    # A retired name must be retired, not merely absent from the roster by accident.
    assert RETIRED_TEAM_IDS.isdisjoint(declared_team_ids())
    assert len(RETIRED_TEAM_IDS) == 3, (
        "the rename table in config/organization.yaml lists three renames; a fourth belongs "
        "in both places or this allowance is wider than the decision it records"
    )


def test_the_retired_names_are_the_ones_the_roster_says_they_are() -> None:
    """Mutation: retire a name here and leave the table in the roster unchanged.

    The table is a comment, so nothing can parse it, and a comment that has stopped matching
    the code is worse than no comment because a reader trusts it. This is the cheapest thing
    that fails when the two drift: the old name and the new one both have to appear in the
    file, on the line the table puts them on.

    Read as text rather than as YAML deliberately. The rename table is not data, and making it
    data would mean adding a field to ``OrganizationInventory`` whose only consumer is a test.
    """
    roster = (PROJECT_ROOT / "config" / "organization.yaml").read_text(encoding="utf-8")
    # The table is aligned on the arrow, so the gap before it is presentation. Collapsed
    # rather than matched, or this test would fail on somebody tidying the column.
    collapsed = " ".join(roster.split())
    renames = {
        "tokenizer": "input-core",
        "modeling": "pre-training",
        "curriculum": "post-training",
    }

    assert set(renames) == set(RETIRED_TEAM_IDS)
    for old, new in renames.items():
        assert f"{old} -> {new}" in collapsed, (
            f"config/organization.yaml does not carry `{old} -> {new}` in its rename table, "
            "so an audit that finds the old name on a record has nothing to read"
        )
        assert new in declared_team_ids()


def test_a_declared_group_names_a_github_team_that_exists() -> None:
    """Mutation: declare a group whose github_team_slug names no team.

    The slug is a claim about another system, and a wrong one fails late and unhelpfully:
    nobody can be added to the team, so the group's members never get repository write, and
    the symptom they report is that the Run button is not there.
    """
    captured = captured_teams()
    declared_slugs = {team.github_team_slug for team in inventory().team_bindings.teams}

    missing = declared_slugs - set(captured.slugs)
    assert missing == set(), (
        f"config/organization.yaml declares {sorted(missing)} as GitHub teams and the "
        "organization holds no team by those names"
    )


def test_a_github_team_that_is_not_a_role_team_is_bound_to_a_group() -> None:
    """The other direction. Mutation: create a team on GitHub and bind it to nothing.

    Separate from the test above because the fix is different. A team nobody declared is
    write access on this repository that no group accounts for: its members can submit, and
    every run they submit is attributed to whatever they type. Deleting a team is not
    reversible, so the remedy is usually the binding rather than the team.
    """
    captured = captured_teams()

    unbound = set(captured.slugs) - {
        team.github_team_slug for team in inventory().team_bindings.teams
    }
    assert unbound == set(), (
        f"{sorted(unbound)} exist as GitHub teams and no group in config/organization.yaml "
        "declares them, so their members can submit runs nothing can attribute"
    )


def test_the_role_teams_are_held_out_of_the_group_comparison() -> None:
    """Mutation: let team-leads or team-members into the capture.

    Neither is a research group. ``team-leads`` is the reviewer on the lead approval gate
    and ``team-members`` is how write access is granted, so both would show up as unbound
    forever and the check above would be a failure nobody could act on. Held out by name,
    and asserted here so that dropping the exclusion fails once rather than confusingly.
    """
    captured = captured_teams()

    assert set(captured.slugs).isdisjoint(ROLE_TEAM_SLUGS)
    assert declared_team_ids().isdisjoint(ROLE_TEAM_SLUGS)


def test_every_group_can_reach_the_repository_the_submission_form_lives_in() -> None:
    """Mutation: create the teams and leave them on pull.

    GitHub shows a manual workflow only to accounts that can write to the repository, so a
    group whose team holds ``pull`` has members who cannot see the submission form at all.
    That failure names nothing: the page other people describe simply is not there.
    """
    captured = captured_teams()

    insufficient = {
        team.team_slug
        for team in captured.teams
        if team.repository_permission not in {"push", "maintain", "admin"}
    }
    assert insufficient == set(), (
        f"{sorted(insufficient)} do not hold write on edu-llm/platform, so their members "
        "cannot see the submission workflow"
    )


def test_the_namespace_a_group_declares_is_the_prefix_the_platform_derives() -> None:
    """Reads BOTH sides. Mutation: edit one namespace, or change output_prefix.

    ``contracts/results.py`` exists because three places once answered where a run's output
    goes and two of them agreed. ``s3_namespace`` is a fourth place, so it is checked
    against the function rather than trusted, and a group whose namespace is written by hand
    to something else would put its outputs where its role does not reach.
    """
    for team in inventory().team_bindings.teams:
        derived = output_prefix(team=team.team_id, run_id="run_x")

        assert derived == f"s3://{team.s3_namespace}/runs/run_x/", (
            f"{team.team_id} declares {team.s3_namespace} and the platform writes to "
            f"{derived}"
        )
        assert team.s3_namespace.startswith(f"{OUTPUTS_BUCKET}/teams/")


def test_no_group_records_a_member_or_a_lead_yet() -> None:
    """The state this change deliberately leaves alone. Mutation: fill a group in by guess.

    Which group a person belongs to is recorded nowhere: not in this repository, not on
    GitHub, and not in any run record. Recording it is each group's lead confirming who is
    in theirs, and a guess is the failure the roster's own comments describe at length,
    because a person filed under the wrong group looks exactly like one filed correctly.

    Pinned so the first assignment is a reviewed edit that has to change this test with it.
    When it does, ``evaluate_authorization`` starts checking that submitter's claim and
    nobody else's, so the line that removes this assertion is the line enforcement begins on.
    """
    bound = inventory().team_bindings.teams

    assert bound, "the roster declares no groups at all"
    assert all(team.member_logins == () for team in bound)
    assert all(team.lead_logins == () for team in bound)


def test_the_captured_groups_hold_nobody_the_roster_does_not_know() -> None:
    """Mutation: add somebody to a research team who is not on the roster.

    A GitHub team is how write access arrives, and admission refuses a submitter who is not
    on the roster with ``submitter_not_in_roster``. Somebody added to a group's team and not
    to the roster therefore gets as far as filling in the form and is refused afterwards,
    which is the sequence the access-request template exists to prevent.
    """
    captured = captured_teams()
    roster = {member.normalized_github_login for member in inventory().members}

    unknown = {
        login
        for team in captured.teams
        for login in team.member_logins
        if login.casefold() not in roster
    }
    assert unknown == set(), (
        f"{sorted(unknown)} are in a research team on GitHub and are not on the roster in "
        "config/organization.yaml, so their submissions are refused after they fill the form"
    )
