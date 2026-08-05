"""The cost report as a reader meets it, against records on disk.

Two things here are about the report being honest rather than about it being correct.

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
from tools.report_run_costs import EXIT_OK, EXIT_UNUSABLE, main, read_records, render

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

INTENT_FIXTURE = (
    PROJECT_ROOT
    / "fixtures"
    / "evidence"
    / "phase-3"
    / "runs"
    / "run_019fa73d-be37-7066-984b-a4bacf194f49"
    / "records"
    / "intent"
    / "run_019fa73d-be37-7066-984b-a4bacf194f49.json"
)

#: The command as it reached AWS Batch in run_019fb4ce, where a pilot user's shell quoting
#: survived into the form field and ``shlex.split`` returned the whole line as one token.
#: ``require_a_shell_command_that_kept_its_quotes`` refuses it now. It did not then.
UNSPLIT_COMMAND = ['python -c "print(\\"hello from a second person\\")"']

RUN_A = "run_019fa73d-be37-7066-984b-a4bacf194f49"
RUN_B = "run_019fa9a6-4460-7095-a358-a1552e250f1b"
UNREADABLE_RUN = "run_019fb4ce-cf24-7028-8eed-a32a28ec2493"

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
    tmp_path: Path, *, intents: list[dict[str, Any]], attempts: list[dict[str, Any]]
) -> Path:
    root = tmp_path / "lineage"
    for prefix, documents, key in (
        ("intent", intents, "run_id"),
        ("attempt", attempts, "attempt_id"),
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
) -> str:
    root = lineage(tmp_path, intents=intents, attempts=attempts)
    parsed_intents, parsed_attempts, unparsed = read_records(root)
    costs = run_costs(
        intents=parsed_intents,
        attempts=parsed_attempts,
        compute_profiles=[compute_profile(ON_DEMAND), compute_profile(SPOT)],
    )
    return render(costs, teams=teams, unparsed=unparsed)


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
# What the section says about the claims the roster disagrees with
# ---------------------------------------------------------------------------------------
#
# The report is the one surface carrying this, and `tools/report_spend.py` prints the figure
# beside the same split without repeating the list. These are about it staying a report:
# saying the size on the team's own line, naming the runs once, and never turning into
# something that refuses or subtracts.


def test_a_run_claimed_against_a_group_the_roster_disagrees_with_is_named_once(
    tmp_path: Path,
) -> None:
    """What replaced the refusal #221 removed, at the place the loss actually lands.

    ``philote-dev`` submitted the fixture record. Recorded on ``platform`` and claiming
    ``memory-split``, this is exactly the case admission used to refuse from inside AWS
    after a lead had already released the run.
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
    )

    assert report.count(RUN_A) == 2, "once in the section below, once in Every run"
    assert (
        "Claimed against a group the roster puts the submitter on a different one from"
        in report
    )
    assert (
        f"- `{RUN_A}` $3.00: philote-dev claimed memory-split and the roster records them "
        "on platform" in report
    )
    assert "$3.00 of that, across 1 run, was claimed by somebody the roster records on " in (
        report
    )


def test_the_section_is_absent_when_the_roster_disagrees_with_nothing(
    tmp_path: Path,
) -> None:
    """A finding printed at zero is a heading a reader learns to skip.

    The standing sentence about how the split is produced stays, because it is true every
    time. The section naming runs appears only when there are runs to name.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split", members=("philote-dev",)),)),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
    )

    assert "a different one from" not in report
    assert "was claimed by somebody the roster records" not in report
    assert "nothing on the platform refuses a claim the roster disagrees with" in report


def test_a_submitter_on_no_recorded_group_is_not_reported_as_misattributing(
    tmp_path: Path,
) -> None:
    """Mutation: report every run whose ``team_verified`` would be false.

    Most of the pilot has no recorded membership, so that reading would put nearly every run
    on this page under a heading accusing its submitter of booking spend to somebody else's
    group. ``tools/report_onboarding_readiness.py`` is where the missing roster lines are
    reported, and the report says so rather than leaving the omission to be noticed.
    """
    report = report_for(
        tmp_path,
        teams=TeamBindingCatalog(teams=(binding("memory-split"),)),
        intents=[intent(RUN_A, team="memory-split")],
        attempts=[attempt(RUN_A)],
    )

    assert "a different one from" not in report


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
