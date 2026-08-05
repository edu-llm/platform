"""The cost report as a reader meets it, against records on disk.

Three things here are about the report being honest rather than about it being correct.

Whether a run's team claim was verified is read off ``team_verified`` on its decision record,
so these write decision records and the report reads a third prefix. It used to work the
answer out again from the roster as the report ran, which named eighteen runs from
2026-08-01 as people charging work to other groups' budgets. Every one of them was admitted
before any group's membership was written down.

The per-team section is reconciled against ``TeamBindingCatalog``, so spend claiming a team
nothing binds has to reach the reader under the name it claimed. Every run recorded so far
is in that position, because ``config/organization.yaml`` carries no ``team_bindings`` yet,
and a section that rendered that as blank would look like a platform nobody had spent
anything on.

The other is the count of records that would not parse. One stored intent record, for
``run_019fb4ce``, holds a command that was valid when it was sealed and is refused by the
rule this tree carries now. The record is immutable and it is not wrong, so it cannot appear
in the arithmetic. What it must do is appear in the count, because a report describing only
the readable subset hides a recorder that has started writing documents nothing can read.

The bindings are built here rather than read out of ``config/organization.yaml``, because
what that file binds is a roster decision that will change and none of this is about the
roster.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.contracts.bindings import TeamBinding, TeamBindingCatalog
from edullm_platform.contracts.workload import ComputeProfile
from edullm_platform.run_costs import run_costs
from tools.report_run_costs import (
    EXIT_OK,
    EXIT_UNUSABLE,
    main,
    read_decisions,
    read_records,
    render,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

PHASE_3_RUN = (
    PROJECT_ROOT
    / "fixtures"
    / "evidence"
    / "phase-3"
    / "runs"
    / "run_019fa73d-be37-7066-984b-a4bacf194f49"
    / "records"
)

INTENT_FIXTURE = PHASE_3_RUN / "intent" / "run_019fa73d-be37-7066-984b-a4bacf194f49.json"

DECISION_FIXTURE = PHASE_3_RUN / "decision" / "run_019fa73d-be37-7066-984b-a4bacf194f49.json"

#: The command as it reached AWS Batch in run_019fb4ce, where a pilot user's shell quoting
#: survived into the form field and ``shlex.split`` returned the whole line as one token.
#: ``require_a_shell_command_that_kept_its_quotes`` refuses it now. It did not then.
UNSPLIT_COMMAND = ['python -c "print(\\"hello from a second person\\")"']

RUN_A = "run_019fa73d-be37-7066-984b-a4bacf194f49"
RUN_B = "run_019fa9a6-4460-7095-a358-a1552e250f1b"
UNREADABLE_RUN = "run_019fb4ce-cf24-7028-8eed-a32a28ec2493"

#: A run that exists only as a decision record, used by :func:`membership_recorded`.
HORIZON_RUN = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"

ON_DEMAND = "gpu-1xa10g"
SPOT = "gpu-1xa10g-spot"


def compute_profile(name: str) -> ComputeProfile:
    return ComputeProfile.model_validate(
        {
            "name": name,
            "instance_type": "g5.xlarge",
            "accelerator": "gpu",
            "nodes": 1,
            "hourly_rate_usd": "3.0000",
            "pricing_source": "test",
            "pricing_observed_at": "2026-07-31",
            "provisioned": True,
        }
    )


def intent(
    run_id: str,
    *,
    team: str,
    compute: str = ON_DEMAND,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """A stored intent record with its team, its profile and its command swapped out.

    Built from a record the platform actually wrote rather than from a literal, so that a
    contract tightened around any other field fails here rather than being fixtured past.
    """
    loaded: Any = json.loads(INTENT_FIXTURE.read_text(encoding="utf-8"))
    loaded["run_id"] = run_id
    loaded["manifest"]["team"] = team
    loaded["manifest"]["compute_profile"] = compute
    if command is not None:
        loaded["manifest"]["command"] = command
    return dict(loaded)


def decision(
    run_id: str,
    *,
    team: str,
    team_verified: bool | None = True,
    recorded_at: str = "2026-07-28T14:07:43.198481Z",
) -> dict[str, Any]:
    """A stored decision record with its claimed team and its verdict swapped out.

    ``team_verified=None`` drops the whole authorization block, which is what a record
    refused for a manifest hash mismatch carries and the only shape the contract permits it
    on. It is a third answer rather than a missing one.

    Built from a record the platform actually wrote, for the reason :func:`intent` gives.
    """
    loaded: Any = json.loads(DECISION_FIXTURE.read_text(encoding="utf-8"))
    loaded["run_id"] = run_id
    loaded["recorded_at"] = recorded_at
    if team_verified is None:
        loaded["authorization"] = None
        loaded["cost"] = None
        loaded["accepted"] = False
        loaded["reason"] = "manifest_hash_mismatch"
        loaded["detail"] = "The manifest presented after approval does not hash to it."
        return dict(loaded)
    loaded["authorization"]["claimed_team"] = team
    loaded["authorization"]["team_verified"] = team_verified
    return dict(loaded)


def membership_recorded() -> dict[str, Any]:
    """A decision record whose only job is to say this submitter was once checkable.

    ``team_verified: false`` means two unlike things, and only a true on one of the same
    submitter's records tells them apart: before the first one, false is the absence of a
    check; after it, false is a check that disagreed. So a test that wants a contradiction
    has to establish that the person was checkable at all, and this is how.

    Given a run id of its own with no intent and no attempt, which is not a contrivance:
    every run admission denied is in the store as a decision with nothing beneath it.
    """
    return decision(
        HORIZON_RUN,
        team="platform",
        team_verified=True,
        recorded_at="2026-07-28T00:00:00.000000Z",
    )


def attempt(run_id: str, *, hours: int = 1) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "attempt_id": f"att_{run_id.removeprefix('run_')}",
        "run_id": run_id,
        "attempt_ordinal": 1,
        "scheduler_job_id": "fde2fa08-a611-48dc-a0ef-1c6797147543",
        "started_at": "2026-07-28T14:00:00.000000Z",
        "ended_at": f"2026-07-28T{14 + hours:02d}:00:00.000000Z",
        "terminal_state": "succeeded",
    }


def lineage(
    tmp_path: Path,
    *,
    intents: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    decisions: list[dict[str, Any]] | None = None,
) -> Path:
    root = tmp_path / "lineage"
    for prefix, documents, key in (
        ("intent", intents, "run_id"),
        ("attempt", attempts, "attempt_id"),
        ("decision", decisions or [], "run_id"),
    ):
        directory = root / prefix
        directory.mkdir(parents=True)
        for document in documents:
            (directory / f"{document[key]}.json").write_text(
                json.dumps(document), encoding="utf-8"
            )
    return root


def binding(
    team_id: str, *, lead: str | None = "ericrcwu001", members: tuple[str, ...] = ()
) -> TeamBinding:
    """A bound team, optionally with nobody recorded as leading it.

    ``lead=None`` is reachable on the shipped roster and was not reachable here, which is
    the whole reason a team with no lead rendered as ``led by )`` for as long as it did.
    ``TeamBinding`` dropped its one-lead minimum on 2026-08-01 and this helper kept it.

    ``members`` defaults to nobody, which is what most of these want: with no membership
    recorded there is nothing to contradict, so the tests above stay about the rollup rather
    than acquiring an opinion about who may claim what.
    """
    return TeamBinding.model_validate(
        {
            "team_id": team_id,
            "github_team_slug": team_id,
            "lead_logins": [] if lead is None else [lead],
            "member_logins": list(members),
            "s3_namespace": f"sbsandbox-intern-{team_id}",
            "wandb_entity": f"edu-llm-{team_id}",
        }
    )


def report_for(
    tmp_path: Path,
    *,
    teams: TeamBindingCatalog,
    intents: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    decisions: list[dict[str, Any]] | None = None,
) -> str:
    root = lineage(tmp_path, intents=intents, attempts=attempts, decisions=decisions)
    parsed_intents, parsed_attempts, unparsed = read_records(root)
    parsed_decisions, unparsed_decisions = read_decisions(root)
    costs = run_costs(
        intents=parsed_intents,
        attempts=parsed_attempts,
        compute_profiles=[compute_profile(ON_DEMAND), compute_profile(SPOT)],
        decisions=parsed_decisions,
    )
    return render(costs, teams=teams, unparsed=unparsed + unparsed_decisions)


# ---------------------------------------------------------------------------------------
# What the per-team section says once it is reconciled against the roster
# ---------------------------------------------------------------------------------------


def test_the_per_team_section_names_the_lead_of_each_bound_team(tmp_path: Path) -> None:
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split", lead="ericrcwu001"),)),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
    )

    assert "- memory-split (@memory-split, led by ericrcwu001): $3.00 across 1 run" in report


def test_a_team_nobody_leads_says_so_rather_than_trailing_off(tmp_path: Path) -> None:
    """Mutation: join ``lead_logins`` unconditionally.

    That is what this did, and it rendered ``(@scratch, led by ): $0.00`` -- a line that
    reads as a roster lookup that returned nothing rather than as a team nobody leads.
    ``scratch`` is the shipped team it happens to, and ``guides/the-platform.md`` tells
    every new person to pick ``scratch`` for their first run, so it is the team most
    likely to appear on this report at all.

    Reachable only since ``TeamBinding`` dropped its one-lead minimum on 2026-08-01. The
    contract moved, the approver page moved with it, and this renderer did not.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("scratch", lead=None),)),
        intents=[intent(RUN_A, team="scratch")],
        attempts=[attempt(RUN_A)],
    )

    assert "- scratch (@scratch, no lead recorded): $3.00 across 1 run" in report
    assert "led by )" not in report


def test_a_bound_team_with_no_runs_is_rendered_at_zero(tmp_path: Path) -> None:
    """Mutation: render only the teams the records mention.

    A team that spent nothing is a different fact from a team nobody has heard of, and only
    one of the two is worth somebody asking a question about.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split"), binding("learning-science"))),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
    )

    assert "- learning-science (@learning-science, led by ericrcwu001): $0.00 across 0 runs" in (
        report
    )


def test_spend_claiming_a_team_nothing_binds_is_reported_under_that_name(
    tmp_path: Path,
) -> None:
    """Mutation: leave the unbound claims out, or add them to a bound team.

    Left out, the report's team lines stop adding up to its total and nobody can say why.
    Added in, somebody else's spend appears on a lead's line and reads exactly like spend
    that lead's group incurred.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split"),)),
        intents=[intent(RUN_A, team="memory-split"), intent(RUN_B, team="tokenizer")],
        attempts=[attempt(RUN_A), attempt(RUN_B)],
    )

    assert "Claimed against a team nothing binds" in report
    assert "- `tokenizer`: $3.00 across 1 run" in report
    assert "routed to a lead or to a cost centre" in report


def test_an_empty_binding_catalog_says_so_rather_than_printing_an_empty_rollup(
    tmp_path: Path,
) -> None:
    """The state ``config/organization.yaml`` is in today, which is not an empty report.

    With no ``team_bindings`` in the roster there is nothing to roll up against, and every
    run lands under the name it claimed. A section that rendered as blank would look like a
    platform on which nobody had spent anything.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
    )

    assert "binds no teams" in report
    assert "Claimed against a team nothing binds" in report
    assert "- `memory-split`: $3.00 across 1 run" in report


def test_a_teams_unpriced_runs_are_counted_beside_its_figure(tmp_path: Path) -> None:
    """Mutation: count only the runs that carry a figure.

    A team whose work is all spot would then read as a team that did nothing, which is the
    reading the unpriced runs exist to prevent everywhere else in this report.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split"),)),
        intents=[
            intent(RUN_A, team="memory-split"),
            intent(RUN_B, team="memory-split", compute=SPOT),
        ],
        attempts=[attempt(RUN_A), attempt(RUN_B)],
    )

    assert "$3.00 across 2 runs, 1 with no figure" in report
    assert "Runs with no figure, and why" in report


# ---------------------------------------------------------------------------------------
# What the section says about the group each run's own record says it claimed
# ---------------------------------------------------------------------------------------
#
# The report is the one surface carrying this, and `tools/report_spend.py` prints the figure
# beside the same split without repeating the list. These are about it staying a report:
# saying the size on the team's own line, naming the runs once, never turning into something
# that refuses or subtracts, and reading the verdict off the record rather than working it
# out again against whatever the roster says this morning.


def test_a_run_whose_record_says_the_claim_was_not_verified_is_named_once(
    tmp_path: Path,
) -> None:
    """What replaced the refusal #221 removed, at the place the loss actually lands.

    ``philote-dev`` submitted the fixture record. Its decision record carries
    ``team_verified: false`` against a claim on ``memory-split``, which is exactly the case
    admission used to refuse from inside AWS after a lead had already released the run.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(
            teams=(
                binding("memory-split", members=("katiehehe",)),
                binding("platform", members=("philote-dev",)),
            )
        ),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
        decisions=[
            membership_recorded(),
            decision(RUN_A, team="memory-split", team_verified=False),
        ],
    )

    assert report.count(RUN_A) == 2, "once in the section below, once in Every run"
    assert "What the decision records say about the group each run claimed" in report
    assert (
        f"- `{RUN_A}` $3.00: philote-dev claimed memory-split and its decision record "
        "carries `team_verified: false`. The roster today records them on platform" in report
    )
    assert (
        "$3.00 of that, across 1 run, carries a decision record saying the claim on it was "
        "never verified" in report
    )
    assert "carry no verdict either way" not in report


def test_a_run_the_record_verified_stays_out_however_the_roster_has_moved_since(
    tmp_path: Path,
) -> None:
    """Mutation: compare the submitter against the roster instead of reading the record.

    This is the failure the section was rebuilt around. The record says the claim was
    checked and matched. The roster since puts ``philote-dev`` somewhere else, because
    people move between groups after their runs are over. Re-asking gets the roster's
    answer to today's question and prints it as a statement about a run that predates the
    edit.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(
            teams=(
                binding("memory-split", members=("katiehehe",)),
                binding("platform", members=("philote-dev",)),
            )
        ),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
        decisions=[decision(RUN_A, team="memory-split", team_verified=True)],
    )

    assert "carries a decision record saying the claim on it was never verified" not in report
    assert "What the decision records say" not in report
    assert "nothing on the platform refuses a claim the roster disagrees with" in report


def test_a_run_whose_record_carries_no_verdict_is_counted_and_named_nowhere(
    tmp_path: Path,
) -> None:
    """The eighteen, and the sentence that keeps their absence from being a clean bill.

    Eighteen runs from 2026-08-01 were admitted before any group's ``member_logins``
    existed. Their records say false because nothing could check anybody, and the first
    reading of the flag printed all eighteen as people charging work to other groups'
    budgets. They belong in neither answer, and the count is what stops that reading as
    every claim having been verified.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split", members=("katiehehe",)),)),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
        decisions=[decision(RUN_A, team="memory-split", team_verified=False)],
    )

    assert "1 run above carries no verdict either way" in report
    assert "No run above carries a decision record saying its team claim was contradicted" in (
        report
    )
    assert RUN_A not in report.split("### What the decision records say")[1].split(
        "## By submitter"
    )[0]


def test_a_record_that_evaluated_no_authorization_is_not_read_as_a_verdict(
    tmp_path: Path,
) -> None:
    """Mutation: read a null authorization block as false.

    ``run_019fa4c0`` in the store is refused for a manifest hash mismatch and carries no
    authorization at all, because nothing derived from an unapproved manifest is
    trustworthy. Reading the absence as false manufactures a finding out of a deliberate
    refusal to make one.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split", members=("katiehehe",)),)),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
        decisions=[decision(RUN_A, team="memory-split", team_verified=None)],
    )

    assert "carries a decision record saying the claim on it was never verified" not in report
    assert "1 run above carries no verdict either way" in report


def test_a_split_with_nothing_to_say_either_way_prints_no_section(tmp_path: Path) -> None:
    """A finding printed at zero is a heading a reader learns to skip.

    The standing sentence about how the split is produced stays, because it is true every
    time. The section appears when a record has something to say or when records are being
    withheld, and not when every claim was verified.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split", members=("philote-dev",)),)),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
        decisions=[decision(RUN_A, team="memory-split", team_verified=True)],
    )

    assert "What the decision records say" not in report
    assert "carry no verdict" not in report


def test_the_contradicted_spend_is_still_inside_the_team_total_it_is_reported_under(
    tmp_path: Path,
) -> None:
    """Mutation: net the doubtful spend off the team's figure.

    The rollup and the total are read side by side and nothing may go missing between them,
    which is what ``test_bound_and_unbound_spend_add_up_to_what_was_priced`` guards one layer
    down. A deduction here would also publish an attribution no record supports.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(
            teams=(
                binding("memory-split", members=("katiehehe",)),
                binding("platform", members=("philote-dev",)),
            )
        ),
        intents=[intent(RUN_A, team="memory-split"), intent(RUN_B, team="memory-split")],
        attempts=[attempt(RUN_A), attempt(RUN_B)],
        decisions=[
            membership_recorded(),
            decision(RUN_A, team="memory-split", team_verified=False),
            decision(RUN_B, team="memory-split", team_verified=False),
        ],
    )

    assert "- memory-split (@memory-split, led by ericrcwu001): $6.00 across 2 runs" in report
    assert "- platform (@platform, led by ericrcwu001): $0.00 across 0 runs" in report
    assert "**2 runs priced, 0 not, $6.00 total.**" in report


# ---------------------------------------------------------------------------------------
# A record this tree can no longer read is counted rather than dropped
# ---------------------------------------------------------------------------------------


def test_a_stored_record_the_current_contract_refuses_is_counted_and_said_out_loud(
    tmp_path: Path,
) -> None:
    """Mutation: skip what will not parse and report the rest.

    ``run_019fb4ce`` carries a command whose quoting survived the form field, valid when the
    record was sealed and refused by this tree. It cannot appear in the arithmetic. It must
    appear in the count, because a store producing documents this tree cannot read is a
    defect in the recorder and a report describing the readable subset hides exactly that.
    """
    root = lineage(
        tmp_path,
        intents=[
            intent(RUN_A, team="memory-split"),
            intent(UNREADABLE_RUN, team="tokenizer", command=UNSPLIT_COMMAND),
        ],
        attempts=[attempt(RUN_A)],
    )

    intents, _attempts, unparsed = read_records(root)

    assert [record.run_id for record in intents] == [RUN_A]
    assert unparsed == 1

    report = render((), teams=TeamBindingCatalog(), unparsed=unparsed)
    assert "1 record did not parse" in report
    assert "valid when it was sealed" in report


def test_the_count_of_refused_records_is_absent_when_every_record_parsed() -> None:
    """The sentence is a finding, so printing it at zero would make it noise."""
    assert "did not parse" not in render((), teams=TeamBindingCatalog(), unparsed=0)


def test_a_record_stored_as_a_string_holding_json_is_read_rather_than_dropped(
    tmp_path: Path,
) -> None:
    """Mutation: keep only the documents that deserialise straight to an object.

    Some records are stored as a JSON string holding JSON, because the state machine writes
    the handler's canonical bytes rather than re-encoding them. Both spellings sit in the
    committed Phase 2 fixtures under one prefix, so this is how the store is rather than a
    corruption.

    The loader used to keep ``dict`` and discard everything else without a word, which put
    such a record in none of the three places a run can be: not priced, not reported
    unpriced, and not in the count that exists so that a missing record is impossible to
    miss. It simply left, and the total it left out of looked complete.
    """
    root = lineage(tmp_path, intents=[intent(RUN_A, team="memory-split")], attempts=[])
    wrapped = intent(RUN_B, team="tokenizer")
    (root / "intent" / f"{RUN_B}.json").write_text(
        json.dumps(json.dumps(wrapped)), encoding="utf-8"
    )

    intents, _attempts, unparsed = read_records(root)

    assert [record.run_id for record in intents] == sorted([RUN_A, RUN_B])
    assert unparsed == 0


def test_a_document_that_is_not_a_record_at_all_is_counted_rather_than_discarded(
    tmp_path: Path,
) -> None:
    """Mutation: silently skip anything that is not an object after unwrapping.

    A stored document that is a bare string, a number or a list is not a record this tree
    can read, which is the same standing as one the contract refuses and belongs in the
    same count. Dropping it instead is the failure the test above describes, reached by a
    different route.
    """
    root = lineage(tmp_path, intents=[intent(RUN_A, team="memory-split")], attempts=[])
    (root / "intent" / "not-a-record.json").write_text(json.dumps("plain text"), encoding="utf-8")

    intents, _attempts, unparsed = read_records(root)

    assert [record.run_id for record in intents] == [RUN_A]
    assert unparsed == 1


# ---------------------------------------------------------------------------------------
# Exit codes: 0 reported, 2 the inputs could not be read, and no 1
# ---------------------------------------------------------------------------------------


def test_a_report_written_to_a_file_exits_zero(tmp_path: Path) -> None:
    root = lineage(
        tmp_path,
        intents=[intent(RUN_A, team="memory-split", compute="cpu-32vcpu")],
        attempts=[attempt(RUN_A)],
    )
    output = tmp_path / "run-costs.md"

    exit_code = main(
        [
            "--lineage-root",
            str(root),
            "--config-dir",
            str(CONFIG_DIR),
            "--output",
            str(output),
        ]
    )

    written = output.read_text(encoding="utf-8")
    assert exit_code == EXIT_OK
    assert "# What runs have cost" in written
    assert "## By team" in written
    assert "## By submitter" in written


def test_a_root_with_no_decision_prefix_is_unusable_rather_than_a_split_with_no_verdicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: carry on with no decisions when the prefix is not there.

    A store where every claim went unverified is a real state with a real report, so a
    reading that never opened ``decision/`` must not be spelled the same way. It would print
    every run as carrying no verdict, which is exactly what the store looked like before
    2026-08-02 and would stay on the page long after it stopped being true.
    """
    root = lineage(tmp_path, intents=[intent(RUN_A, team="memory-split")], attempts=[])
    shutil.rmtree(root / "decision")

    exit_code = main(["--lineage-root", str(root), "--config-dir", str(CONFIG_DIR)])

    assert exit_code == EXIT_UNUSABLE
    assert "no decision/ directory" in capsys.readouterr().err


def test_a_lineage_root_holding_no_records_is_unusable_rather_than_an_empty_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        ["--lineage-root", str(tmp_path / "absent"), "--config-dir", str(CONFIG_DIR)]
    )

    assert exit_code == EXIT_UNUSABLE
    assert "no intent/ directory" in capsys.readouterr().err


def test_a_config_directory_without_the_roster_is_unusable_rather_than_an_unbound_rollup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: treat an unreadable organization.yaml as an empty binding catalog.

    An empty catalog is a real state of the roster with a report of its own, so a failure to
    read the file must not be spelled the same way. It would render as every team being
    unbound, which is the correct answer today and would stay on the page long after it
    stopped being one.
    """
    root = lineage(
        tmp_path,
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
    )
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(CONFIG_DIR / "workload-catalog.yaml", config / "workload-catalog.yaml")

    exit_code = main(["--lineage-root", str(root), "--config-dir", str(config)])

    assert exit_code == EXIT_UNUSABLE
    assert capsys.readouterr().err.strip() != ""
