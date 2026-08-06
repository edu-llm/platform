"""The approval gate's membership against the roster that describes it."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def verifier():  # type: ignore[no-untyped-def]
    """The tool, imported by path because ``tools/`` is not a package.

    Cached in ``sys.modules`` for the reason ``tests/module_identity.py`` gives.
    """
    cached = sys.modules.get("verify_the_lead_team_matches_the_roster")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "verify_the_lead_team_matches_the_roster",
        PROJECT_ROOT / "tools" / "verify_the_lead_team_matches_the_roster.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_the_lead_team_matches_the_roster"] = module
    spec.loader.exec_module(module)
    return module


def test_somebody_on_the_team_the_roster_names_nowhere_is_a_widening() -> None:
    """Mutation: report the count difference and let the reader work out who.

    This is the finding the whole check exists for. Somebody holding the lead gate who the
    roster names neither lead nor admin can approve other people's spending, and the only
    place that fact is written down is the GitHub UI.
    """
    found = verifier().compare(
        on_the_team=["ada", "grace", "stranger"],
        declared_leads=["ada", "grace"],
        declared_admins=["root"],
    )

    assert found.can_approve_unnamed == ("stranger",)
    assert found.agree is False


def test_a_declared_admin_on_the_team_is_named_and_is_not_drift() -> None:
    """Mutation: count the admin as a widening, which is how this check cries wolf.

    An admin already holds `run-approval-admin`. Approving as a lead grants them nothing they
    did not have, so treating it as a fault would make the check red on a correct account and
    somebody would turn it off. It is printed rather than hidden, because a reader should see
    why the two counts differ.
    """
    found = verifier().compare(
        on_the_team=["ada", "root"],
        declared_leads=["ada"],
        declared_admins=["root"],
    )

    assert found.admins_on_the_team == ("root",)
    assert found.can_approve_unnamed == ()
    assert found.agree is True


def test_a_named_lead_who_is_not_on_the_team_cannot_approve_what_reaches_them() -> None:
    """Mutation: look only for the widening, which is the half a security review asks for.

    A run routed to the lead environment sits there. Nothing is over-permitted and the person
    named to act has no button, which is a stuck queue rather than a breach and is still a
    fault.
    """
    found = verifier().compare(
        on_the_team=["ada"],
        declared_leads=["ada", "grace"],
        declared_admins=[],
    )

    assert found.named_and_cannot_approve == ("grace",)
    assert found.agree is False


def test_a_login_that_differs_only_in_case_is_one_person() -> None:
    """Mutation: compare the strings as typed.

    GitHub treats a login case-insensitively and the roster is typed by hand, so a difference
    in case is this check inventing a fault in both directions at once.
    """
    found = verifier().compare(
        on_the_team=["BritishAmericqn"],
        declared_leads=["britishamericqn"],
        declared_admins=[],
    )

    assert found.can_approve_unnamed == ()
    assert found.named_and_cannot_approve == ()
    assert found.agree is True


def test_the_two_lists_agreeing_exactly_is_agreement() -> None:
    found = verifier().compare(
        on_the_team=["ada", "grace"],
        declared_leads=["grace", "ada"],
        declared_admins=[],
    )

    assert found.agree is True
    assert found.admins_on_the_team == ()


def test_a_login_declared_both_lead_and_admin_is_reported_as_the_lead() -> None:
    """Mutation: test membership of the admins first, or report the person in both columns.

    The roster declares `philote-dev` a lead and an admin, which is ordinary and which the
    first draft of this test assumed away. Somebody in both sets is a named lead, so they are
    not drift and they are not one of the admins that explains a count difference either.
    Printing them under the admin line would tell a reader the counts differ because of them
    when the counts do not differ because of them at all.
    """
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    both = sorted(set(inventory.team_leads) & set(inventory.admins))
    assert both, "this test is about the overlap and the roster has stopped having one"

    found = verifier().compare(
        on_the_team=both,
        declared_leads=inventory.team_leads,
        declared_admins=inventory.admins,
    )

    assert found.admins_on_the_team == ()
    assert found.can_approve_unnamed == ()


def test_a_team_that_cannot_be_read_is_two_rather_than_a_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: treat a failed `gh api` as an empty team, which reports every lead as absent.

    This is the defect that made the stage board untrustworthy this morning, in miniature. An
    empty listing is an answer and a failed call is not, and a check that confuses them
    reports its loudest finding exactly when it knows least.
    """
    module = verifier()
    monkeypatch.setattr(
        module,
        "_team_members",
        lambda organization, team: (_ for _ in ()).throw(RuntimeError("gh: not logged in")),
    )

    assert module.main([]) == 2


def test_a_widening_exits_one_and_names_the_person(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = verifier()
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    monkeypatch.setattr(
        module,
        "_team_members",
        lambda organization, team: [*inventory.team_leads, "somebody-nobody-declared"],
    )

    assert module.main([]) == 1
    assert "somebody-nobody-declared can approve as a lead" in capsys.readouterr().out


def test_the_team_as_the_roster_declares_it_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = verifier()
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    monkeypatch.setattr(
        module, "_team_members", lambda organization, team: list(inventory.team_leads)
    )

    assert module.main([]) == 0
    assert "Nobody can approve who the roster does not allow" in capsys.readouterr().out


def test_the_admin_sitting_on_the_team_today_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The account as measured on 2026-08-06, which is nine against eight and is fine."""
    module = verifier()
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    monkeypatch.setattr(
        module,
        "_team_members",
        lambda organization, team: [*inventory.team_leads, inventory.admins[-1]],
    )

    assert module.main([]) == 0
    printed = capsys.readouterr().out
    assert "is on the team and is a declared admin" in printed


def test_the_call_is_gh_bounded_and_reaches_no_aws_endpoint() -> None:
    """Mutation: reach for boto3, drop the timeout, or let a nonzero exit raise past main.

    A GitHub team is a GitHub fact. This runs on the token a scheduled workflow already holds
    and needs no AWS role at all, which is what lets it sit on the audit beside `report_asks`.
    The timeout is the other half: a `gh` waiting on a dead token would wedge the job rather
    than fail it.
    """
    source = (PROJECT_ROOT / "tools" / "verify_the_lead_team_matches_the_roster.py").read_text(
        encoding="utf-8"
    )

    assert "boto3" not in source
    assert '"gh"' in source
    assert "timeout=90" in source
    assert "check=False" in source
