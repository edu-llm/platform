"""The scheduled half of the lead gate check, which is the age rather than the comparison.

tests/test_phase2_github_evidence.py already compares the committed capture of `team-leads`
against the roster in both directions, on every push. This module covers the thing that check
structurally cannot do: notice that the capture it is trusting was taken by hand and has been
sitting in the tree since. FreshEvidenceModel refuses one after thirty days and thirty days is
exactly how long the ninth member of that team went unnoticed, which config/organization.yaml
records in its own words.

The tool reads two committed files and no network, so every test here is a real end-to-end run
of it rather than a mocked one. What is faked is the tree, not the reader.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from tools.report_who_can_open_the_lead_gate import (
    CAPTURE_PATH,
    CAPTURE_UNREADABLE,
    INVENTORY_PATH,
    LEAD_GATE_CONFIG_PATH,
    capture_stands,
    days_old,
    main,
    who_the_gate_and_the_roster_disagree_about,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def tree(
    tmp_path: Path,
    *,
    member_logins: list[str] | None = None,
    observed_days_ago: float = 1.0,
    stands_for_days: int = 7,
) -> Path:
    """A checkout holding this repository's real roster and a capture the caller chooses.

    THE ROSTER IS THE REAL ONE AND THAT IS DELIBERATE. The set the gate has to match is
    `admins | team_leads` read through holds_routine_approver_role, and a fixture roster would
    let this module agree with a rule the platform does not use -- which is the exact mistake
    tests/test_phase2_github_evidence.py records making when it compared against `team_leads`
    alone. Only the capture and the threshold are invented here.
    """
    (tmp_path / "config" / "reports").mkdir(parents=True)
    (tmp_path / "fixtures" / "evidence" / "phase-2" / "github").mkdir(parents=True)

    (tmp_path / INVENTORY_PATH).write_text(
        (PROJECT_ROOT / INVENTORY_PATH).read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / LEAD_GATE_CONFIG_PATH).write_text(
        yaml.safe_dump({"schema_version": 1, "capture_stands_for_days": stands_for_days}),
        encoding="utf-8",
    )

    if member_logins is None:
        member_logins = json.loads((PROJECT_ROOT / CAPTURE_PATH).read_text(encoding="utf-8"))[
            "member_logins"
        ]
    observed_at = datetime.now(tz=UTC) - timedelta(days=observed_days_ago)
    (tmp_path / CAPTURE_PATH).write_text(
        json.dumps(
            {
                "environment": "sandbox",
                "member_logins": member_logins,
                "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                "organization": "edu-llm",
                "repository": "platform",
                "source": "github",
                "team_slug": "team-leads",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def run(root: Path) -> int:
    return main(["--project-root", str(root)])


def test_a_fresh_capture_that_matches_the_roster_is_clean(tmp_path: Path) -> None:
    """Mutation: report a finding when there is none.

    The committed capture and the committed roster agree today, so this is the run the audit
    should be getting every morning. A check that is red on a clean tree is one nobody reads on
    the morning it means something.
    """
    assert run(tree(tmp_path)) == 0


def test_a_capture_older_than_the_threshold_is_red_even_when_it_agrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: report only the disagreement and let any age pass.

    THIS IS THE WHOLE REASON THIS TOOL EXISTS. A capture that agrees with the roster is
    evidence about the day it was taken and nothing else, because who is in a GitHub team can
    be changed in a browser in ten seconds leaving no artifact anywhere. Without this the
    schedule reports agreement forever and the only thing that ever asks again is
    FreshEvidenceModel at thirty days, which is how long the ninth member went unnoticed.
    """
    assert run(tree(tmp_path, observed_days_ago=9.0, stands_for_days=7)) == 1

    assert "no longer stands" in capsys.readouterr().out


def test_the_threshold_is_inclusive_at_its_own_edge() -> None:
    """Mutation: use a strict comparison, so a capture of exactly the stated age is refused.

    config/reports/lead-gate.yaml says a capture of exactly this age still stands. A strict
    comparison moves the window by a day without anybody deciding to move it, which is the
    reasoning config/reports/asks.yaml gives for its own inclusive threshold.

    ASSERTED AGAINST THE PREDICATE AND NOT THROUGH A TREE, WHICH IS WHY THE PREDICATE EXISTS.
    The report subtracts from `datetime.now`, so a capture written to be exactly seven days old
    is a few microseconds past seven by the time it is read and a strict comparison passes
    anyway. This mutation survived a whole-tool test and is the reason the comparison was
    lifted out of the report.
    """
    assert capture_stands(7.0, stands_for_days=7) is True
    assert capture_stands(7.000001, stands_for_days=7) is False


def test_somebody_on_the_team_the_roster_does_not_authorize_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: compare in one direction only.

    This login opens `run-approval-lead` and is then refused by admission with
    `approver_lacks_lead_or_admin_role`. The approval is spent, the run is not released, and
    the failure looks like a permissions bug rather than two lists disagreeing.
    """
    logins = json.loads((PROJECT_ROOT / CAPTURE_PATH).read_text(encoding="utf-8"))
    root = tree(tmp_path, member_logins=[*logins["member_logins"], "arteexu"])

    assert run(root) == 1

    printed = capsys.readouterr().out
    assert "not a routine approver" in printed
    assert "arteexu" in printed


def test_an_approver_the_team_does_not_hold_is_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: compare in the other direction only.

    The opposite incident and it fails the other way round. The lead gate can never route to
    this person, their own group's runs included, and `can_admins_bypass` is false on that
    environment so being an admin does not rescue it. Both directions were live at once for the
    two days ending 2026-07-30.
    """
    logins = json.loads((PROJECT_ROOT / CAPTURE_PATH).read_text(encoding="utf-8"))
    kept = [login for login in logins["member_logins"] if login != "ericrcwu001"]

    assert run(tree(tmp_path, member_logins=kept)) == 1

    printed = capsys.readouterr().out
    assert "will never ask" in printed
    assert "ericrcwu001" in printed


def test_a_capture_that_cannot_be_read_is_exit_two_and_not_a_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: read an absent or malformed capture as a clean comparison.

    A comparison that was not made says nothing about the lead gate in either direction, and it
    needs the opposite repair from a comparison that found something. Both look identical on a
    board carrying one red cross, so the code separates them the way
    tools/report_roster_against_the_account.py separates a refused read from an empty one.
    """
    root = tree(tmp_path)
    (root / CAPTURE_PATH).write_text("{}", encoding="utf-8")

    assert run(root) == 2

    assert CAPTURE_UNREADABLE in capsys.readouterr().err


def test_the_comparison_is_not_case_sensitive_about_a_login(tmp_path: Path) -> None:
    """Mutation: compare the raw strings.

    GitHub is case-insensitive about logins and returns them however the account was created,
    while the roster is written the way people spell their own names. A comparison over raw
    strings reports every login whose case differs as drift in both directions at once, which
    is a finding that cannot be repaired by editing either list.
    """
    logins = json.loads((PROJECT_ROOT / CAPTURE_PATH).read_text(encoding="utf-8"))
    shouted = [login.upper() for login in logins["member_logins"]]

    assert run(tree(tmp_path, member_logins=shouted)) == 0


def test_the_two_directions_are_reported_separately() -> None:
    """Mutation: return one set holding the symmetric difference.

    Merging them loses which repair each name needs. One is removed from a GitHub team or given
    a group to lead, the other is added to a GitHub team by the owner, and a single list of
    names leaves whoever reads it to work out which is which by memory.
    """
    unauthorized, unasked = who_the_gate_and_the_roster_disagree_about(
        member_logins=["philote-dev", "arteexu"], approvers=frozenset({"philote-dev", "meric233"})
    )

    assert unauthorized == ("arteexu",)
    assert unasked == ("meric233",)


def test_an_age_is_reported_at_better_than_whole_days() -> None:
    """Mutation: floor the age to whole days.

    The report prints the age out loud, and a capture taken thirty-six hours ago reading as
    "1 day old" is a number somebody argues with instead of acting on. The comparison against
    the threshold runs at the same precision, so the two never disagree about the same capture.
    """
    now = datetime(2026, 8, 6, 12, tzinfo=UTC)

    assert days_old(now - timedelta(hours=36), now=now) == 1.5
