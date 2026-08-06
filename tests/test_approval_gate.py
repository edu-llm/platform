"""That the gate declared in this repository is the gate GitHub is being held to.

``src/edullm_platform/approval_gate.py`` is the first place this repository writes down what
the approval environments must be. Until it existed the answer was in organization settings
and in a capture with a thirty-day expiry, so between the two there was a window in which
nothing could say what the gate was — and the whole of what stops a run from spending money
lives in that window.

Every test below names the mutation it was written against. That convention is not
decoration here: the subject is a set of comparisons whose failure mode is agreeing with
everything, and a comparison that cannot disagree is worth less than no comparison, because
it is read as coverage. Each mutation was applied to the source and each of these went red
before it was written down.

The payloads are the shape ``GET /repos/{owner}/{repo}/environments/{name}`` actually
returned for this repository on 2026-08-06, reduced to the keys the reader consumes. They are
built here rather than committed under ``fixtures/evidence/`` on purpose: these are not
observations of the account and must not be read as any, they are the response *shape* the
parser is written against, and a fixture with a freshness window would make this module fail
for a reason having nothing to do with the parser.

**Two endpoints and two comparisons, because one environment's answer lives in each.** The
body above carries the two boolean forms of the deployment branch policy and not the branch
patterns under them; those come from ``…/environments/{name}/deployment-branch-policies``.
That is why :func:`compare_the_branch_policy` is separate from :func:`compare_gate` rather
than a clause inside it — a caller that reached only the first endpoint would otherwise
report a gate as checked while the one setting that says which branches may deploy to it went
unread. That setting is how the fourth environment stayed invisible: three gates are pinned
to ``main`` and ``run-approval-preview`` is ``*``, and nothing anywhere read either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest

from edullm_platform import approval_gate
from edullm_platform.approval_gate import (
    DECLARED_ENVIRONMENT_NAMES,
    DECLARED_GATES,
    LEAD_APPROVAL_GATE,
    PREVIEW_GATE,
    compare_gate,
    compare_lead_team_membership,
    compare_the_branch_policy,
    compare_the_environment_list,
    compare_visibility,
    declared_gate,
    read_branch_policy_names,
    read_environment,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.authorization import holds_routine_approver_role
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.phase2_evidence import APPROVAL_ENVIRONMENT_NAMES, LEAD_APPROVAL_TEAM_SLUG

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROSTER_PATH = PROJECT_ROOT / "config" / "organization.yaml"


@pytest.fixture(scope="module")
def inventory() -> OrganizationInventory:
    return load_yaml(ROSTER_PATH, OrganizationInventory)


@pytest.fixture(scope="module")
def roster_admins(inventory: OrganizationInventory) -> tuple[str, ...]:
    return tuple(str(login) for login in inventory.admins)


@pytest.fixture(scope="module")
def routine_approvers(inventory: OrganizationInventory) -> tuple[str, ...]:
    """Everybody admission accepts as the approver of a routine run.

    Asked of ``holds_routine_approver_role`` rather than assembled from ``team_leads``,
    because that function is what admission consults and assembling it here would be the
    second spelling whose drift this module exists to catch.
    """
    return tuple(
        member.github_login
        for member in inventory.members
        if holds_routine_approver_role(inventory, member.github_login)
    )


#: What GitHub returns under ``deployment_branch_policy`` for all four of this repository's
#: environments, read from the account on 2026-08-06. The *names* under it are not here: they
#: come from ``…/deployment-branch-policies``, which is a second call and a second comparison,
#: and the four environments do not agree on them.
NAMED_BRANCH_POLICY: Final[dict[str, bool]] = {
    "protected_branches": False,
    "custom_branch_policies": True,
}


def team_reviewed(slug: str = LEAD_APPROVAL_TEAM_SLUG, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": LEAD_APPROVAL_GATE,
        "can_admins_bypass": False,
        "deployment_branch_policy": dict(NAMED_BRANCH_POLICY),
        "protection_rules": [
            {
                "id": 60981222,
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [{"type": "Team", "reviewer": {"name": slug, "slug": slug}}],
            },
            {"id": 60981223, "type": "branch_policy"},
        ],
    }
    payload.update(overrides)
    return payload


def user_reviewed(*logins: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "run-approval-admin",
        "can_admins_bypass": False,
        "deployment_branch_policy": dict(NAMED_BRANCH_POLICY),
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": False,
                "reviewers": [
                    {"type": "User", "reviewer": {"login": login}} for login in logins
                ],
            },
            {"type": "branch_policy"},
        ],
    }
    payload.update(overrides)
    return payload


def unreviewed(name: str = "run-approval-automatic", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "can_admins_bypass": False,
        "deployment_branch_policy": dict(NAMED_BRANCH_POLICY),
        "protection_rules": [{"type": "branch_policy"}],
    }
    payload.update(overrides)
    return payload


def reasons(findings: object) -> list[str]:
    return [finding.reason for finding in findings]  # type: ignore[union-attr]


def test_the_declaration_covers_every_environment_the_trust_policy_enumerates() -> None:
    """The declared list is not allowed to be shorter than the one AWS already trusts.

    Mutation: delete the ``run-approval-lead`` entry from ``DECLARED_GATES``. Nothing else in
    the suite objects — the tool would simply stop asking about the gate that stops routine
    runs and go on printing green for the other three, which is the exact shape of check this
    repository refuses. Applied, and this goes red.
    """
    declared = {gate.name for gate in DECLARED_GATES}
    assert set(APPROVAL_ENVIRONMENT_NAMES) <= declared

    # And every environment a class can route to, asked of the enum that does the routing
    # rather than listed here, so a fourth approval class cannot arrive undeclared.
    for environment in ApprovalEnvironment:
        assert environment.value in declared, environment


def test_the_two_gates_that_must_ask_a_person_are_declared_as_asking_one() -> None:
    """Reviewer-required is a property of the declaration and not of the account.

    Mutation: set ``reviewer_team_slugs=()`` on the lead gate's declaration, which is what
    somebody "simplifying" it would do. The tool then reports a live gate with no reviewers
    as correct, which is the same state as the repository having been made private. Applied,
    and this goes red.
    """
    required = {gate.name for gate in DECLARED_GATES if gate.reviewers_required}
    assert required == {LEAD_APPROVAL_GATE, "run-approval-admin"}


def test_a_gate_that_has_stopped_asking_anybody_is_reported(roster_admins: tuple[str, ...]) -> None:
    """The reading this whole module exists for, and the one that is an absence.

    This is what converting the repository to private does on this plan: the protection rule
    is removed, the waiting job proceeds, and there is no error anywhere. It is also what an
    admin deleting the rule by hand does.

    Mutation: make ``compare_gate`` skip the check when ``has_required_reviewer_rule`` is
    false, which is the natural shape of "only compare the reviewers when there are
    reviewers". Applied, and this goes red.
    """
    gate = declared_gate(LEAD_APPROVAL_GATE)
    assert gate is not None

    findings = compare_gate(gate, read_environment(unreviewed(name=LEAD_APPROVAL_GATE)), roster_admins)

    assert "the_gate_no_longer_asks_anybody" in reasons(findings)


def test_a_reviewerless_gate_that_acquired_a_reviewer_is_reported(
    roster_admins: tuple[str, ...],
) -> None:
    """The automatic gate having no reviewer is a declared property, not an accident.

    Mutation: drop the ``not declared.reviewers_required and live.has_required_reviewer_rule``
    branch. Runs the policy says need no human then start waiting for one with no symptom
    other than a queue, and the trust policy still matches so nothing else notices. Applied,
    and this goes red.
    """
    gate = declared_gate("run-approval-automatic")
    assert gate is not None
    acquired = team_reviewed()
    acquired["name"] = "run-approval-automatic"

    findings = compare_gate(gate, read_environment(acquired), roster_admins)

    assert "an_unreviewed_gate_acquired_a_reviewer" in reasons(findings)


def test_a_swapped_reviewer_team_is_reported(roster_admins: tuple[str, ...]) -> None:
    """Who stands behind a team slot is organization state and appears in no commit.

    Mutation: compare the number of reviewer teams rather than their slugs. One team swapped
    for another is then a pass, and a wholly different set of approvers holds the gate with
    no diff anywhere in this repository. Applied, and this goes red.
    """
    gate = declared_gate(LEAD_APPROVAL_GATE)
    assert gate is not None

    findings = compare_gate(gate, read_environment(team_reviewed(slug="everyone")), roster_admins)

    assert "the_reviewer_team_moved" in reasons(findings)


def test_named_reviewers_are_held_to_the_roster_in_both_directions(
    roster_admins: tuple[str, ...],
) -> None:
    """The admin gate names people, so it is the one reviewer set a schedule can reconcile.

    Both directions in one test because they are one comparison here: the admin gate's
    reviewers are read live, and set equality against ``config/organization.yaml`` is the
    whole check. The lead gate's directions are two findings and are tested separately,
    because there the two are different incidents.

    Mutation: compare with ``<=`` instead of ``==``. An extra named reviewer on the admin
    gate — somebody who can release an exception and is not an admin — then passes. Applied,
    and this goes red.
    """
    gate = declared_gate("run-approval-admin")
    assert gate is not None

    agreeing = compare_gate(gate, read_environment(user_reviewed(*roster_admins)), roster_admins)
    assert reasons(agreeing) == []

    extra = compare_gate(
        gate, read_environment(user_reviewed(*roster_admins, "somebody-else")), roster_admins
    )
    assert "the_named_reviewers_are_not_the_roster" in reasons(extra)

    missing = compare_gate(
        gate, read_environment(user_reviewed(roster_admins[0])), roster_admins
    )
    assert "the_named_reviewers_are_not_the_roster" in reasons(missing)


def test_prevent_self_review_moving_is_reported(roster_admins: tuple[str, ...]) -> None:
    """Turning it on is the change the owner is expected to make, and it must leave a commit.

    Reported rather than welcomed, and the finding says which line to move. That is the point
    of declaring it: a change to who may release a run currently happens in a browser and
    exists nowhere a reviewer can read it, and this is what turns it into a diff.

    Mutation: drop the ``prevent_self_review`` comparison. The setting then moves in either
    direction with nothing recorded, which is the state before this module. Applied, and this
    goes red.
    """
    gate = declared_gate(LEAD_APPROVAL_GATE)
    assert gate is not None
    hardened = team_reviewed()
    hardened["protection_rules"][0]["prevent_self_review"] = True

    findings = compare_gate(gate, read_environment(hardened), roster_admins)

    assert "prevent_self_review_moved" in reasons(findings)


def test_an_admin_bypass_is_reported_and_an_absent_field_counts_as_one(
    roster_admins: tuple[str, ...],
) -> None:
    """A bypassed job leaves no approval record at all, so admission has no approver to read.

    The absent-field half is the mutation that matters. ``payload.get("can_admins_bypass",
    False)`` is the spelling somebody writes without thinking, and it turns a response this
    parser does not understand into a pass on the one setting whose failure removes the
    attribution rather than widening it.

    Mutation: default the field to ``False``. Applied, and the second assertion goes red.
    """
    gate = declared_gate(LEAD_APPROVAL_GATE)
    assert gate is not None

    explicit = team_reviewed(can_admins_bypass=True)
    assert "an_admin_may_release_without_a_reviewer" in reasons(
        compare_gate(gate, read_environment(explicit), roster_admins)
    )

    absent = team_reviewed()
    del absent["can_admins_bypass"]
    assert "an_admin_may_release_without_a_reviewer" in reasons(
        compare_gate(gate, read_environment(absent), roster_admins)
    )


def test_the_branch_policy_form_is_held_to_the_named_one_on_every_gate(
    roster_admins: tuple[str, ...],
) -> None:
    """Which branches may deploy is a control, and until 2026-08-06 nothing read it live.

    The two forms are not equivalent and the distinction is the whole reason both flags are
    asserted rather than a summary. ``protected_branches`` defers to whatever branch
    protection happens to cover, so an environment on that form widens the moment somebody
    protects a second branch — for a reason nobody would connect to this control, and leaving
    the declared patterns with nothing to be the answer to.

    Mutation: read a missing ``deployment_branch_policy`` as the named form, which is the
    forgiving default somebody writes to stop a sparse payload reporting. An environment with
    no branch policy at all accepts a deployment from every branch, so the one shape that
    means "unrestricted" would be the one shape that passes. Applied, and the third assertion
    goes red.
    """
    gate = declared_gate(LEAD_APPROVAL_GATE)
    assert gate is not None

    assert reasons(compare_gate(gate, read_environment(team_reviewed()), roster_admins)) == []

    inherited = team_reviewed()
    inherited["deployment_branch_policy"] = {
        "protected_branches": True,
        "custom_branch_policies": False,
    }
    assert "the_branch_policy_is_not_the_named_form" in reasons(
        compare_gate(gate, read_environment(inherited), roster_admins)
    )

    absent = team_reviewed()
    del absent["deployment_branch_policy"]
    assert "the_branch_policy_is_not_the_named_form" in reasons(
        compare_gate(gate, read_environment(absent), roster_admins)
    )


def test_the_branch_patterns_are_declared_per_gate_and_the_four_disagree() -> None:
    """The setting the fourth environment differs on, and the reason a blanket check fails.

    Three gates admit ``main`` and ``run-approval-preview`` admits ``*``. Asserting one answer
    across all four is what ``tests/test_phase2_github_evidence.py`` did until 2026-08-06, and
    it was a red waiting for whoever next re-captured — a red whose only clearing edit is the
    one that stops checking the other three.

    Mutation: declare ``("main",)`` on the preview gate, which is what "tightening" it looks
    like. Every role trusted to ``submit-run.yml`` pins its subject to ``refs/heads/main``, so
    the preview path then has no environment a branch dispatch can enter and the submission
    path goes back to being the one path nobody can exercise before merging it. Applied, and
    the last assertion goes red.
    """
    pinned = declared_gate(LEAD_APPROVAL_GATE)
    preview = declared_gate(PREVIEW_GATE)
    assert pinned is not None and preview is not None

    assert compare_the_branch_policy(pinned, ("main",)) == ()
    assert reasons(compare_the_branch_policy(pinned, ("main", "release/*"))) == [
        "the_branch_policy_moved"
    ]
    assert reasons(compare_the_branch_policy(pinned, ())) == ["the_branch_policy_moved"]

    assert compare_the_branch_policy(preview, ("*",)) == ()
    assert reasons(compare_the_branch_policy(preview, ("main",))) == ["the_branch_policy_moved"]


def test_a_tag_pattern_is_not_read_as_a_branch_that_may_deploy() -> None:
    """The endpoint answers both kinds and only one of them restricts a deployment.

    Mutation: drop the ``type`` filter from ``read_branch_policy_names``. A gate whose only
    branch entry had been replaced by a tag entry named ``main`` then reads as pinned to
    ``main`` while admitting every branch — the exact reading this whole module exists to
    refuse, arrived at through a key nobody thought about. Applied, and the last assertion
    goes red.
    """
    assert read_branch_policy_names({"branch_policies": [{"name": "main", "type": "branch"}]}) == (
        "main",
    )

    # Absent ``type`` reads as a branch, because that is what the endpoint returned before
    # tag policies existed and a missing key is not a tag.
    assert read_branch_policy_names({"branch_policies": [{"name": "main"}]}) == ("main",)

    assert read_branch_policy_names({"branch_policies": []}) == ()
    assert read_branch_policy_names({}) == ()
    assert read_branch_policy_names({"branch_policies": [{"name": "main", "type": "tag"}]}) == ()


def test_the_two_environment_lists_are_named_apart_and_only_one_means_all_of_them() -> None:
    """The mistake this branch was opened for, written down where it cannot be made again.

    ``phase2_evidence.APPROVAL_ENVIRONMENT_NAMES`` is the three subjects the admission role's
    trust policy enumerates. ``DECLARED_ENVIRONMENT_NAMES`` is every environment on the
    repository. The two coincided until 2026-08-04, were used interchangeably because of it,
    and stopped coinciding when ``run-approval-preview`` was created — leaving three equality
    assertions in ``tests/test_phase2_github_evidence.py`` that would go red on whoever next
    refreshed the evidence, for a correct reason, arguing for a wrong repair.

    ``tests/test_run_preview_role.py::test_the_preview_environment_is_not_one_of_the_admission
    _gates`` holds the other end: the preview gate must never enter the trust enumeration,
    because the preview role exists precisely so a branch reaches something other than
    admission. So this is not drift to be closed. It is two sets that are permanently
    different and were spelled as one.

    Mutation: define ``DECLARED_ENVIRONMENT_NAMES`` as ``APPROVAL_ENVIRONMENT_NAMES``, which
    is what somebody "removing a duplicate list" would do. Applied, and this goes red.
    """
    assert set(APPROVAL_ENVIRONMENT_NAMES) < set(DECLARED_ENVIRONMENT_NAMES)
    assert set(DECLARED_ENVIRONMENT_NAMES) - set(APPROVAL_ENVIRONMENT_NAMES) == {PREVIEW_GATE}
    assert PREVIEW_GATE not in APPROVAL_ENVIRONMENT_NAMES

    # Derived from the declaration rather than written beside it, so a fifth environment
    # cannot be declared into one and left out of the other.
    assert DECLARED_ENVIRONMENT_NAMES == tuple(gate.name for gate in DECLARED_GATES)


def test_every_comparison_this_module_exports_is_one_the_scheduled_tool_makes() -> None:
    """A comparison nothing calls is a check that reports nothing, and looks like coverage.

    Written as a sweep over ``__all__`` rather than as a list of the five, because the failure
    it exists for is the sixth: somebody adds a comparison here, tests it thoroughly, and
    never wires it into ``tools/verify_the_gate.py`` — whereupon the daily job goes on
    printing green while the new setting is unread, and the tests for it all pass. That is
    precisely how the deployment branch policy came to be declared nowhere and read by
    nothing while three environments were being watched closely.

    ``compare_lead_team_membership`` is reached only behind ``--check-team-membership`` and is
    still named in the source, which is what this asserts. Whether the scheduled run makes it
    is a different question and the tool answers it in its own words, in the paragraph it
    prints when the flag is absent.

    Mutation: delete the ``compare_the_branch_policy`` call from ``main``. The suite is
    otherwise silent about it — every test above drives the pure function directly. Applied,
    and this goes red naming it.
    """
    source = (PROJECT_ROOT / "tools" / "verify_the_gate.py").read_text(encoding="utf-8")
    exported = [name for name in approval_gate.__all__ if name.startswith("compare_")]

    assert len(exported) >= 5
    uncalled = sorted(name for name in exported if f"{name}(" not in source)
    assert not uncalled, (
        "exported by edullm_platform.approval_gate and called nowhere in "
        f"tools/verify_the_gate.py, so nothing reads it every morning: {uncalled}"
    )


def test_a_private_repository_below_team_is_reported() -> None:
    """The highest-consequence footgun in the system, and it is one click with no error.

    Mutation: add the unreadable plan to
    ``PLANS_CARRYING_THE_GATE_ON_A_PRIVATE_REPOSITORY``, which is what somebody writes to stop
    the scheduled run reporting a plan its token cannot see. A private repository whose plan
    nobody could establish then reads as safe, and the one reading that does not depend on
    believing GitHub's documented behaviour is gone. Applied, and this goes red.
    """
    assert reasons(compare_visibility("private", "free")) == [
        "the_gate_depends_on_public_visibility"
    ]
    assert reasons(compare_visibility("internal", "free")) == [
        "the_gate_depends_on_public_visibility"
    ]

    # A plan this tool could not read is not a plan that carries the control.
    assert reasons(compare_visibility("private", "unknown")) == [
        "the_gate_depends_on_public_visibility"
    ]

    # And the two escapes, so the check cannot be one that always fires either.
    assert compare_visibility("public", "free") == ()
    assert compare_visibility("private", "team") == ()
    assert compare_visibility("private", "enterprise") == ()


def test_an_environment_nobody_declared_is_reported() -> None:
    """An environment is created with no protection rules by anybody who names one.

    Everybody who can submit holds the write access that allows it, so the door is one
    workflow file away. It cannot reach AWS, because the admission role's trust policy
    enumerates its subjects — but nothing else would say it is there.

    Mutation: compare only ``declared - seen`` and drop ``seen - declared``, which is the
    direction somebody writes when the question in their head is "is anything missing". The
    open door then passes. Applied, and this goes red.
    """
    live = [gate.name for gate in DECLARED_GATES] + ["run-approval-oops"]

    findings = compare_the_environment_list(live)

    assert reasons(findings) == ["undeclared_environment"]
    assert "run-approval-oops" in findings[0].message


def test_a_declared_environment_that_vanished_is_reported() -> None:
    """A workflow routing to an absent environment deploys to one GitHub creates on the spot.

    That environment has no protection rules, so the run this gate was supposed to hold does
    not wait for anybody. Deleting an environment is therefore the same outcome as deleting
    its reviewer rule, reached by a different click.

    Mutation: drop the ``declared - seen`` direction. Applied, and this goes red.
    """
    live = [gate.name for gate in DECLARED_GATES if gate.name != LEAD_APPROVAL_GATE]

    findings = compare_the_environment_list(live)

    assert reasons(findings) == ["declared_environment_is_gone"]


def test_the_membership_comparison_reports_both_directions_as_separate_findings() -> None:
    """Somebody GitHub asks who admission refuses, and somebody admission accepts unasked.

    Separate findings because they are separate incidents with separate fixes, and because
    the first one is the worst ordering available: the refusal lands after the click, so the
    approval has already been spent on a run that then dies.

    Mutation: report a single ``membership_disagrees`` finding for either direction. A reader
    then has to work out which way round it is before they know whether to edit the roster or
    the team. Applied, and this goes red.
    """
    findings = compare_lead_team_membership(
        member_logins=("a-lead", "a-stranger"),
        routine_approvers=("a-lead", "an-unasked-admin"),
    )

    assert reasons(findings) == [
        "the_gate_asks_somebody_admission_will_refuse",
        "an_approver_the_gate_will_never_ask",
    ]
    assert "a-stranger" in findings[0].message
    assert "an-unasked-admin" in findings[1].message


def test_the_membership_comparison_is_against_admins_and_leads_rather_than_leads_alone(
    inventory: OrganizationInventory,
    routine_approvers: tuple[str, ...],
) -> None:
    """The set the gate has to match is ``holds_routine_approver_role``, not ``team_leads``.

    This is the reading that made the reviewer team look wrong when it was right. The roster
    names eight leads and the team has nine members, and the ninth is an admin — whom
    admission accepts and whom ``team_leads`` does not name. Compared against ``team_leads``
    the ninth reads as drift, and the only edit that silences that reading is adding him to
    ``team_leads``, which ``tests/test_inventory.py`` refuses because he leads no group. So
    the wrong comparison points at a repair the roster will not accept.

    Mutation: pass ``inventory.team_leads`` as the accepted set. Applied, and the second
    assertion goes red, naming the admins as approvers the gate will never ask.
    """
    admins = {str(login) for login in inventory.admins}
    assert admins - {str(login) for login in inventory.team_leads}, (
        "this test is only meaningful while at least one admin leads no group"
    )

    every_approver_on_the_team = compare_lead_team_membership(
        member_logins=routine_approvers, routine_approvers=routine_approvers
    )
    assert every_approver_on_the_team == ()

    leads_only = compare_lead_team_membership(
        member_logins=routine_approvers,
        routine_approvers=tuple(str(login) for login in inventory.team_leads),
    )
    assert "the_gate_asks_somebody_admission_will_refuse" in reasons(leads_only)


def test_the_comparison_is_case_insensitive_the_way_admission_is() -> None:
    """GitHub logins are case-preserving and case-insensitive, and admission normalizes.

    Mutation: compare the raw strings. ``BritishAmericqn`` typed as ``britishamericqn``
    anywhere then reads as two different people, and the check reports drift that is not
    there — which is worse than silence, because the repair for a finding that is wrong is to
    stop reading the finding.
    """
    assert compare_lead_team_membership(("BritishAmericqn",), ("britishamericqn",)) == ()


def test_the_shipped_declaration_agrees_with_what_github_answered(
    roster_admins: tuple[str, ...],
) -> None:
    """The declaration is checked against a real response shape rather than only against itself.

    Every payload here is what this repository's own environments returned on 2026-08-06,
    reduced to the consumed keys. Without this the module is a set of comparisons against
    hand-built inputs, and the first thing a change to GitHub's response shape would break is
    the parser rather than any assertion.

    Mutation: have ``read_environment`` look for ``rule["reviewers"]`` at the top level of the
    payload rather than inside the ``required_reviewers`` rule. Every gate then reads as
    having no reviewers, which is the failure that reports the live gate as deleted. Applied,
    and this goes red.
    """
    live = {
        "run-approval-automatic": unreviewed(),
        LEAD_APPROVAL_GATE: team_reviewed(),
        "run-approval-admin": user_reviewed(*roster_admins),
        PREVIEW_GATE: unreviewed(name=PREVIEW_GATE),
    }
    # What ``…/deployment-branch-policies`` answered for each of the four on the same day,
    # and the one reading among all of these where the fourth environment differs from its
    # siblings. Kept beside the bodies rather than folded into them because it is a second
    # call, and a test that merged the two would let a caller reach only the first and still
    # look covered.
    live_branch_policies = {
        "run-approval-automatic": {"branch_policies": [{"name": "main", "type": "branch"}]},
        LEAD_APPROVAL_GATE: {"branch_policies": [{"name": "main", "type": "branch"}]},
        "run-approval-admin": {"branch_policies": [{"name": "main", "type": "branch"}]},
        PREVIEW_GATE: {"branch_policies": [{"name": "*", "type": "branch"}]},
    }

    assert compare_the_environment_list(live) == ()
    assert set(live) == set(DECLARED_ENVIRONMENT_NAMES)
    for name, payload in live.items():
        gate = declared_gate(name)
        assert gate is not None, name
        assert compare_gate(gate, read_environment(payload), roster_admins) == (), name
        assert (
            compare_the_branch_policy(
                gate, read_branch_policy_names(live_branch_policies[name])
            )
            == ()
        ), name
