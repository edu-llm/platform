"""The board that joins W&B, the account and the outputs bucket, and reports the disagreements.

Three systems each hold a third of the truth about a run and nothing joins them, so a run can
be in one and absent from the other two indefinitely. The union is trivial and worthless; the
three disagreements are the product, and most of this module is about the ways a board can
report one of them wrongly.

**The way it can lie that matters most is by not having looked.** If W&B cannot be reached,
every run in the account is trivially not in W&B, and a board that printed that would file
sixty-three accusations of unlogged spend on the morning a credential lapsed. So a source is
read into a value or into ``None``, ``None`` means nobody looked, and a comparison whose sides
are not both present is skipped and named rather than answered from one side. Several tests
here exist only to hold that line.

**The second way is by matching the join key too tightly.** Nothing on this platform sets a
W&B run's name, so the run id arrives in W&B in whatever spelling the workload's training
command produced. Read live on 2026-08-02 the eduLLM entity held four spellings, and the
fixtures here are those four rather than invented ones. Six runs are called the literal
``$EDULLM_RUN_ID`` because a command quoted the variable, two carry a ``-died`` suffix, two
carry the id under a config key, and thirteen are named exactly. A board matching only the
last of those would have reported three runs that logged perfectly well as unlogged spend.

**The third is by matching it too loosely**, which is the same failure with the sign flipped.
A resume loads another run's checkpoint and carries that run's id in its config, so a scan
that took the first id it found would credit the wrong run with having logged and quietly
clear a real finding.

The IAM statement the account side is read under is asserted here as well, against the report
that quotes it and against the template that grants it. The value of quoting a statement in a
report is that somebody can apply it, so a statement that does not parse is prose and two
spellings of one statement are a role and a report drifting apart.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from infrastructure_support import ACCOUNT_LITERAL, IAM_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from visibility_board import (
    BINDING_PREFIX,
    DEGRADING_LINEAGE_PREFIXES,
    EXIT_DISAGREES,
    EXIT_OK,
    EXIT_UNUSABLE,
    MISSING_BINDING_GRANT,
    MISSING_TAG_GRANT,
    PLATFORM_TAG_KEYS,
    REQUIRED_LINEAGE_PREFIXES,
    RUN_ID_TAG,
    Board,
    BoundRun,
    Match,
    OutputPrefix,
    SourceGap,
    TaggedResource,
    WandbRun,
    build_board,
    read_binding_records,
    read_output_prefixes,
    read_tagged_resources,
    render,
    run_id_of,
    team_runs_prefix,
)
from wandb_reconciliation import WandbReference, observe

from edullm_platform.capture_tooling import CaptureFailedError
from edullm_platform.contracts.execution import BatchJobBinding
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix

EXECUTION = PROJECT_ROOT / "src" / "edullm_platform" / "execution.py"
ROLE_PATH = IAM_ROOT / "audit-reader-role.yaml"

#: Three run ids off the account, so that a fixture cannot pass by being shaped unlike
#: anything real. The first two are runs whose W&B name is the literal ``$EDULLM_RUN_ID`` and
#: the third is one named exactly.
UNNAMED_RUN = "run_019fbe0c-e689-70b2-a42f-79d742a60c6c"
DIED_RUN = "run_019fc085-2ec3-7035-aea4-37c6c651de5f"
NAMED_RUN = "run_019fbd28-b600-70fa-879b-34fafcd8fe68"

#: A prefix in the outputs bucket whose directory name is a uuid4 rather than a uuid7, so no
#: run id can ever match it. It holds 216 objects and nine gibibytes, which is why a board
#: that skipped what it could not join would be skipping the largest single thing in the
#: bucket.
UUID4_PREFIX = "run_db0f3291-e538-4860-ae87-4266c5f2b36a"

#: The documentation account id, which is the only twelve-digit run
#: ``tests/test_evidence.py`` permits in the tracked tree. Any other one reads there as a real
#: account and that scan does not care that this file is a test.
DOCUMENTATION_ACCOUNT = "123456789012"

#: What ``config.trainer`` carries for a run whose W&B name never expanded. The run id is in
#: the save folder because the platform put it there, which is what makes it recoverable.
SAVE_FOLDER = {
    "trainer": {
        "value": {
            "save_folder": f"{output_prefix(team='platform', run_id=UNNAMED_RUN)}checkpoints/"
        }
    }
}


def a_wandb_run(
    run_id: str | None,
    match: Match,
    *,
    project: str = "edullm-platform",
    state: str = "finished",
    display: str = "",
) -> WandbRun:
    return WandbRun(
        project=project,
        path="abcd1234",
        display_name=display or (run_id or "unnamed"),
        state=state,
        run_id=run_id,
        match=match,
    )


def a_resource(run_id: str | None, *, team: str = "platform") -> TaggedResource:
    return TaggedResource(
        service="batch",
        identifier="job/56b43cb9-abcc-4f74-bbf5-6f61f12d1981",
        run_id=run_id,
        team=team,
        submitter="philote-dev",
        experiment="read-path-proof",
        compute_profile="gpu-1xa10g",
    )


def a_prefix(segment: str, *, team: str = "platform", objects: int = 4) -> OutputPrefix:
    return OutputPrefix(team=team, segment=segment, objects=objects, bytes=15317 * objects)


def a_binding(run_id: str, *, profile: str = "gpu-1xa10g", day: int = 28) -> BoundRun:
    return BoundRun(
        run_id=run_id,
        compute_profile=profile,
        submitted_at=datetime(2026, 7, day, 5, 24, 39, tzinfo=UTC),
    )


def a_binding_record(run_id: str, *, profile: str = "cpu-32vcpu") -> dict[str, Any]:
    """A stored binding built through the contract, so a fixture cannot outlive the shape."""
    record = BatchJobBinding(
        schema_version=1,
        run_id=run_id,
        batch_job_id="caddfe44-daaa-4469-b185-609c708b02de",
        batch_job_arn=(
            f"arn:aws:batch:us-east-1:{DOCUMENTATION_ACCOUNT}:job/"
            "caddfe44-daaa-4469-b185-609c708b02de"
        ),
        batch_job_name=run_id,
        job_queue_arn=(
            f"arn:aws:batch:us-east-1:{DOCUMENTATION_ACCOUNT}:job-queue/"
            "sbsandbox-intern-edullm-cpu"
        ),
        job_definition_arn=(
            f"arn:aws:batch:us-east-1:{DOCUMENTATION_ACCOUNT}:job-definition/"
            "sbsandbox-intern-edullm-cpu-run"
        ),
        compute_profile=profile,
        log_group="/aws/batch/sbsandbox-intern-edullm-cpu",
        attempt_duration_seconds=3600,
        attempts=1,
        array_size=None,
        submitted_at="2026-07-28T05:24:39.892Z",
    )
    return json.loads(record.model_dump_json())


def a_binding_tree(root: Path, records: dict[str, Any]) -> Path:
    directory = root / BINDING_PREFIX
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in records.items():
        (directory / f"{name}.json").write_text(
            body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
        )
    return root


# ----------------------------------------------------------------------------------------
# The tags are the ones the submitter actually sets
# ----------------------------------------------------------------------------------------


def test_the_board_filters_on_every_tag_the_submission_sets() -> None:
    """Mutation: add a sixth tag to execution.py and leave this board reading five.

    The tag keys are composed as a dict literal inside ``batch_submit_request`` and exported
    as no constant, and this board is not allowed to edit that module to acquire one, so the
    two lists are separate and can drift. They are held together here by reading the literals
    out of the source rather than by restating them, which is how every other seam in this
    repository is held. A tag this board does not filter on is a resource it cannot see, and a
    board that cannot see a resource reports its spend as belonging to nobody.
    """
    literals = {
        node.value
        for node in ast.walk(ast.parse(EXECUTION.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("edullm:")
    }

    assert literals, "execution.py sets no edullm tag, which would make this board pointless"
    assert set(PLATFORM_TAG_KEYS) == literals
    assert RUN_ID_TAG in literals


# ----------------------------------------------------------------------------------------
# The join key, in the four spellings the account actually holds
# ----------------------------------------------------------------------------------------


def test_a_run_named_after_its_run_id_is_a_named_match() -> None:
    """The spelling the workloads follow when they follow one, and the only navigable one."""
    assert run_id_of(NAMED_RUN, {}) == (NAMED_RUN, Match.NAMED)


def test_a_run_id_under_a_config_key_is_matched_and_is_not_named() -> None:
    """Mutation: grade a config key as NAMED, since the value is exact either way.

    Exactness is not what the grade is about. A named run is one somebody searching W&B for
    the run id finds, and a config key is not searchable that way, so grading it NAMED would
    mean the board stops reporting runs that nobody can navigate to.
    """
    assert run_id_of("stage2-eval", {"run_id": {"value": NAMED_RUN}}) == (
        NAMED_RUN,
        Match.DERIVED,
    )


def test_a_run_whose_name_never_expanded_is_still_matched_from_its_save_folder() -> None:
    """THE ONE THAT MATTERS. Mutation: match the display name and nothing else.

    Six runs in the entity are called the literal `$EDULLM_RUN_ID`, which is what a training
    command quoting the variable in single quotes produces. All six logged and all six are
    reachable only through the save folder the platform put in their trainer config. A board
    matching names alone reports the three runs behind them as spend nobody can see a loss
    curve for, which is a false accusation against a submitter who did nothing wrong.
    """
    assert run_id_of("$EDULLM_RUN_ID", SAVE_FOLDER) == (UNNAMED_RUN, Match.DERIVED)


def test_a_name_with_a_suffix_glued_on_is_matched_from_the_name() -> None:
    """Two runs in the entity carry a `-died` suffix, which no exact comparison survives."""
    assert run_id_of(f"{DIED_RUN}-died", {}) == (DIED_RUN, Match.DERIVED)


def test_a_record_naming_two_runs_is_joined_to_neither() -> None:
    """THE ONE THAT MATTERS. Mutation: take the first run id the scan finds.

    A resume loads another run's checkpoint and carries that run's id in its config, so the
    loose pass can see two. Picking one is a coin toss that clears a real finding half the
    time and credits the wrong team with a loss curve the other half, and a wrong attribution
    is indistinguishable from a right one once it is printed.
    """
    both = {
        "trainer": {"value": {"save_folder": f"s3://bucket/{UNNAMED_RUN}/", "load": DIED_RUN}}
    }

    assert run_id_of("resumed", both) == (None, Match.NONE)


def test_a_run_naming_no_platform_run_is_not_forced_into_one() -> None:
    """197 of the entity's 218 runs are work that never went through this platform."""
    assert run_id_of("stage1_scratch_verify_1x_pilot_c2", {"lr": {"value": 0.0003}}) == (
        None,
        Match.NONE,
    )


# ----------------------------------------------------------------------------------------
# A source that was not read produces no findings
# ----------------------------------------------------------------------------------------


def a_gap() -> SourceGap:
    return SourceGap(
        source="the account",
        reason="tagged_resources_not_read",
        detail="the call was refused",
        unanswered=("which runs logged nothing",),
        remedy=MISSING_TAG_GRANT,
    )


def test_an_unreachable_wandb_accuses_nobody_of_unlogged_spend() -> None:
    """THE ONE THAT MATTERS. Mutation: treat an unread source as an empty one.

    It is one character of difference in the code and it turns the board into a machine for
    manufacturing findings. With W&B unread, every run in the account is trivially absent from
    W&B, so the board would print sixty-three rows of unlogged spend, name a submitter against
    each, and be wrong about all of them. Both comparisons that need W&B come back as None
    rather than as everything, and the report says which questions stopped being answerable.
    """
    board = build_board(
        wandb_runs=None,
        resources=[a_resource(NAMED_RUN), a_resource(UNNAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        gaps=[
            SourceGap(
                source="Weights and Biases",
                reason="wandb_not_read",
                detail="W&B did not answer",
                unanswered=("which runs logged nothing",),
            )
        ],
    )

    assert board.in_account_not_in_wandb is None
    assert board.in_wandb_with_no_output is None
    assert board.output_with_no_wandb_run is None
    assert board.agreeing is None

    report = render(board)

    assert "## Spend nobody can see a loss curve for" not in report
    assert UNNAMED_RUN not in report
    assert "What this run could not read" in report


def test_agreement_is_not_claimed_from_two_sources_out_of_three() -> None:
    """Mutation: count the runs the two readable sources share and call it agreement.

    Agreement is a statement about all three, and a count taken over two of them reads as the
    three. It is the one number on the page a reader is meant to be able to skip past without
    thinking, so it has to be either true or absent.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=None,
        outputs=[a_prefix(NAMED_RUN)],
        gaps=[a_gap()],
    )

    assert board.agreeing is None
    assert "Not counted" in render(board)


def test_a_source_that_could_not_be_read_is_never_reported_as_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutation: exit 0 when nothing disagreed, whatever went unread.

    A check that could not look is not a check that found nothing, which is the rule every
    scheduled job in this repository follows. Exit 2 is what stops a denied grant reading as
    an account in good order on every morning after it lapses.
    """
    board = build_board(wandb_runs=None, resources=None, outputs=[], gaps=[a_gap()])

    assert not board.disagrees
    assert _exit_for(board, monkeypatch, tmp_path) == EXIT_UNUSABLE


def test_a_finding_outranks_an_unanswered_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutation: return 2 whenever anything went unread, even beside a real finding.

    `tools/verify_deployed_stacks.py` settled this and the reasoning carries over. Somebody
    holding an output prefix nothing can trace has to go and look at it whatever happened to
    the other source, and what could not be read is at the top of the board rather than
    encoded in a number. Reporting the unanswered question instead would demote the one row
    on the page that needs a person.
    """
    board = build_board(
        wandb_runs=[],
        resources=None,
        outputs=[a_prefix(UUID4_PREFIX)],
        gaps=[a_gap()],
    )

    assert board.disagrees
    assert _exit_for(board, monkeypatch, tmp_path) == EXIT_DISAGREES


def test_three_sources_that_agree_are_a_clean_board(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other side, so the exit code cannot pass this file by never being zero."""
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        costs={},
    )

    assert not board.disagrees
    assert board.agreeing == 1
    assert _exit_for(board, monkeypatch, tmp_path) == EXIT_OK


def _exit_for(board: Board, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> int:
    """Run ``main`` over a board that is already assembled, so only the exit rule is tested."""
    import visibility_board

    monkeypatch.setattr(visibility_board, "_collect", lambda _: board)
    return visibility_board.main(["--output", str(tmp_path / "board.md")])


# ----------------------------------------------------------------------------------------
# The three findings
# ----------------------------------------------------------------------------------------


def test_a_run_that_only_the_account_knows_about_is_unlogged_spend() -> None:
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN), a_resource(UNNAMED_RUN, team="memory-split")],
        outputs=[a_prefix(NAMED_RUN)],
    )

    assert board.in_account_not_in_wandb == (UNNAMED_RUN,)

    report = render(board)

    assert "## Spend nobody can see a loss curve for" in report
    assert UNNAMED_RUN in report
    assert "memory-split" in report


def test_a_run_matched_only_from_its_config_is_not_reported_as_unlogged() -> None:
    """THE ONE THAT MATTERS. Mutation: count a DERIVED match as no match.

    It is the tempting tightening, because a derived match is a scan and a scan feels weaker
    than a name. The three runs behind the six `$EDULLM_RUN_ID` records logged every step they
    took, and reporting them as spend nobody can see a loss curve for would send somebody to
    ask a submitter why they turned logging off. The finding they do carry is the different
    one below, which is that nobody can find them by run id.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(UNNAMED_RUN, Match.DERIVED, display="$EDULLM_RUN_ID")],
        resources=[a_resource(UNNAMED_RUN)],
        outputs=[a_prefix(UNNAMED_RUN)],
    )

    assert board.in_account_not_in_wandb == ()

    report = render(board)

    assert "## Spend nobody can see a loss curve for" not in report
    assert "## Logged under something other than the run id" in report
    assert "$EDULLM_RUN_ID" in report


def test_a_run_that_logged_and_wrote_nothing_is_reported() -> None:
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN)],
        outputs=[],
    )

    assert board.in_wandb_with_no_output == (NAMED_RUN,)
    assert "## Runs that saved nothing" in render(board)


def test_one_team_nobody_could_list_withdraws_the_saved_nothing_claim() -> None:
    """THE ONE THAT MATTERS. Mutation: report it anyway, since most teams did answer.

    This finding is an accusation built out of an absence, and an absence produced by a
    refused listing looks exactly like an absence produced by a run that saved nothing. One
    unlisted team would put every run that team has ever submitted into the table.

    The finding below it survives the same refusal, and the two are deliberately not written
    the same way. A prefix that was seen with no run behind it is there whatever went
    unlisted, so a partial listing under-reports it and cannot invent it.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN)],
        outputs=[a_prefix(UUID4_PREFIX)],
        refused_teams=["memory-split"],
    )

    assert board.in_wandb_with_no_output is None
    assert board.agreeing is None
    assert board.untraceable_outputs, "the finding that survives a partial listing still does"

    report = render(board)

    assert "## Runs that saved nothing" not in report
    assert "## Output nobody can trace back to a config" in report


def test_a_finished_run_that_saved_nothing_is_listed_above_a_crashed_one() -> None:
    """Mutation: list them in run id order, since both are in the table either way.

    A run W&B recorded as crashed has already reported its own failure with an exit code, and
    an empty prefix under it is the shape everybody expects. The one worth a morning is the
    run that says `finished` and wrote nothing, and a table that buries it four rows down
    among runs that are behaving as designed is a table somebody skims.
    """
    board = build_board(
        wandb_runs=[
            a_wandb_run(DIED_RUN, Match.DERIVED, state="crashed"),
            a_wandb_run(NAMED_RUN, Match.NAMED, state="finished"),
        ],
        resources=[a_resource(NAMED_RUN), a_resource(DIED_RUN)],
        outputs=[],
    )

    report = render(board)

    assert report.index(NAMED_RUN) < report.index(DIED_RUN)


def test_an_output_prefix_no_wandb_run_names_is_reported() -> None:
    board = build_board(
        wandb_runs=[],
        resources=[a_resource(NAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
    )

    assert board.output_with_no_wandb_run == (NAMED_RUN,)
    assert "## Output nobody can trace back to a config" in render(board)


def test_a_prefix_that_is_not_a_run_id_at_all_is_a_finding_of_its_own() -> None:
    """Mutation: skip a directory whose name does not parse, since it cannot be joined.

    That is the reason to report it rather than the reason to drop it. The largest single
    thing in the outputs bucket is a nine gibibyte prefix named with a uuid4, which no run id
    can ever match, and a board that indexed only what indexed cleanly would leave it out of a
    report whose entire subject is things nobody can account for.
    """
    board = build_board(
        wandb_runs=[],
        resources=[],
        outputs=[a_prefix(UUID4_PREFIX, objects=216), a_prefix("smoke-classify-d", team="x")],
    )

    assert {prefix.segment for prefix in board.untraceable_outputs} == {
        UUID4_PREFIX,
        "smoke-classify-d",
    }
    assert board.disagrees

    report = render(board)

    assert "the directory name is not a run id" in report
    assert UUID4_PREFIX in report


def test_a_tagged_resource_carrying_no_run_id_is_spend_belonging_to_nothing() -> None:
    """Mutation: filter the account read on `edullm:run-id` and be done.

    The tagging API ANDs its filters, so a single filtered call is the natural implementation
    and it makes this case unobservable by construction. A resource carrying our team tag and
    no run id is spend that is ours and belongs to nothing, which is the worst version of the
    first finding rather than an edge case.
    """
    board = build_board(wandb_runs=[], resources=[a_resource(None)], outputs=[])

    assert len(board.untagged_account) == 1
    assert board.disagrees
    assert "## Resources tagged as ours that carry no run id" in render(board)


# ----------------------------------------------------------------------------------------
# The bucket, and the teams that have never written anything
# ----------------------------------------------------------------------------------------


def fake_aws(answers: dict[str, tuple[int, str]]) -> Any:
    """Stand in for the CLI, keyed by the prefix or tag key the call asks for."""

    class Completed:
        def __init__(self, returncode: int, stdout: str) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = "" if returncode == 0 else "An error occurred (AccessDenied) when..."

    def call(arguments: Any, *, profile: Any = None, region: Any = None) -> Any:
        asked = next((item for item in arguments if item in answers), None)
        if asked is None:
            return Completed(0, json.dumps({}))
        return Completed(*answers[asked])

    return call


def test_a_team_that_has_never_written_anything_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ONE THAT MATTERS. Mutation: treat an empty listing as a missing prefix.

    Four of the eight declared teams have written nothing, and `input-core` and `pre-training`
    have never written anything at all. S3 has no directories, so a prefix under which nothing
    was written does not exist and answers with no contents. A board that read that as a
    failure would be red every night for a reason that is the normal state of the account, and
    a job that is red every night is a job nobody reads.
    """
    import visibility_board

    monkeypatch.setattr(visibility_board, "aws", fake_aws({}))

    prefixes, refused = read_output_prefixes(
        ["pre-training", "input-core"], profile=None, region="us-east-1"
    )

    assert prefixes == ()
    assert refused == ()


def test_a_refused_listing_is_not_the_same_as_a_team_that_wrote_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: fold a denial into the empty case, since both produce no prefixes.

    They are opposite statements. One says this team has written nothing and the other says
    nobody was allowed to look, and only the first is a claim about the account. Collapsing
    them would let a lapsed grant read as a quiet team.
    """
    import visibility_board

    monkeypatch.setattr(
        visibility_board, "aws", fake_aws({team_runs_prefix("platform"): (255, "")})
    )

    prefixes, refused = read_output_prefixes(["platform"], profile=None, region="us-east-1")

    assert prefixes == ()
    assert refused == ("platform",)


def test_the_bucket_is_listed_under_the_prefix_shape_the_role_can_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: list `teams/` once instead of one prefix per team.

    It is the obvious simplification and it is denied. The audit reader role conditions
    `s3:ListBucket` with `StringLike` on `teams/*/runs/*`, so a request whose prefix is
    `teams/` matches nothing and comes back as an access denial at 05:00. One call per team
    sends `teams/{team}/runs/`, which the trailing wildcard covers.

    Both sides are read so that widening either one alone fails here.
    """
    import visibility_board

    asked: list[str] = []

    def record(arguments: Any, *, profile: Any = None, region: Any = None) -> Any:
        asked.append(arguments[arguments.index("--prefix") + 1])

        class Completed:
            returncode = 0
            stdout = json.dumps({})
            stderr = ""

        return Completed()

    monkeypatch.setattr(visibility_board, "aws", record)
    read_output_prefixes(["platform", "scratch"], profile=None, region="us-east-1")

    assert asked == ["teams/platform/runs/", "teams/scratch/runs/"]

    granted = {
        statement["Condition"]["StringLike"]["s3:prefix"]
        for properties in load_template(ROLE_PATH)["Resources"].values()
        for policy in properties["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if OUTPUTS_BUCKET in str(statement["Resource"]) and "Condition" in statement
    }

    assert granted == {"teams/*/runs/*"}
    # StringLike's `*` matches zero or more characters, so the trailing one covers the empty
    # tail of `teams/platform/runs/`. That is the whole reason this shape is listable at all
    # and it is one character away from not being.
    condition = re.compile(granted.pop().replace("*", "[^\n]*"))
    for prefix in asked:
        assert condition.fullmatch(prefix), prefix


def test_the_run_directory_shape_comes_from_the_contract_that_composes_it() -> None:
    """Mutation: write `teams/{team}/runs/` here as a literal.

    Three places answered this question once and two of them agreed, which is the whole
    argument in `output_prefix`. The IAM condition this board lists under is written against
    exactly that shape, so a fourth spelling fails as an access denial rather than as anything
    a reader can diagnose.
    """
    composed = output_prefix(team="memory-split", run_id=NAMED_RUN)

    assert composed == f"s3://{OUTPUTS_BUCKET}/{team_runs_prefix('memory-split')}{NAMED_RUN}/"


def test_every_tag_key_is_asked_for_separately_and_unioned_by_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: send all five keys in one call, which reads like the same question.

    It is not. The tagging API ANDs its filters, so one call naming five keys asks for
    resources carrying all five, and two of the five are conditional on the submission having
    a value. What comes back is the subset of runs that named an experiment and recorded a
    submitter, which is a different population reported under the name of the whole one.
    """
    import visibility_board

    asked: list[str] = []

    def record(arguments: Any, *, profile: Any = None, region: Any = None) -> Any:
        asked.append(arguments[arguments.index("--tag-filters") + 1])
        body = {
            "ResourceTagMappingList": [
                {
                    "ResourceARN": f"arn:aws:batch:us-east-1:{DOCUMENTATION_ACCOUNT}:job/56b4",
                    "Tags": [{"Key": asked[-1].removeprefix("Key="), "Value": "seen"}],
                }
            ]
        }

        class Completed:
            returncode = 0
            stdout = json.dumps(body)
            stderr = ""

        return Completed()

    monkeypatch.setattr(visibility_board, "aws", record)
    found = read_tagged_resources(profile=None, region="us-east-1")

    assert asked == [f"Key={key}" for key in PLATFORM_TAG_KEYS]
    assert len(found) == 1, "one resource answering five calls is one resource"
    assert found[0].team == "seen", "the union keeps a tag that only one of the calls returned"


def test_a_refused_tag_read_is_raised_rather_than_read_as_an_empty_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: return what came back and let a denial look like an account with no runs.

    An empty account is a claim and a denial is not, and the two are one `if` apart. Reading
    the second as the first would report every W&B run and every output prefix as belonging to
    a run that never ran.
    """
    import visibility_board

    monkeypatch.setattr(
        visibility_board, "aws", fake_aws({f"Key={RUN_ID_TAG}": (255, "")})
    )

    with pytest.raises(CaptureFailedError) as refused:
        read_tagged_resources(profile=None, region="us-east-1")

    assert "AccessDenied" in str(refused.value)


# ----------------------------------------------------------------------------------------
# What the report is for
# ----------------------------------------------------------------------------------------


def test_the_disagreements_come_before_the_agreement() -> None:
    """Mutation: lead with the population and put the mismatches underneath it.

    A board that opens by listing every run and reporting that most of them are fine is a page
    somebody scrolls. The three rows that need a person are the reason it runs, so they are
    above the fold and the majority is one number at the bottom.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN), a_resource(UNNAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
    )

    report = render(board)

    assert report.index("Spend nobody can see") < report.index("## What agrees")


def test_the_agreeing_majority_is_a_count_and_not_a_list() -> None:
    """Mutation: list the runs that agree, for completeness.

    Completeness is what makes a report unreadable. Fifteen agreeing runs today is a hundred
    next term, and the rows somebody has to act on end up below them.
    """
    agreeing = [f"run_019fbd{index:02d}-b600-70fa-879b-34fafcd8fe68" for index in range(10)]
    board = build_board(
        wandb_runs=[a_wandb_run(run_id, Match.NAMED) for run_id in agreeing],
        resources=[a_resource(run_id) for run_id in agreeing],
        outputs=[a_prefix(run_id) for run_id in agreeing],
    )

    report = render(board)

    assert board.agreeing == 10
    assert "10 run(s) are in all three" in report
    for run_id in agreeing:
        assert run_id not in report


def test_the_board_never_prints_an_account_id() -> None:
    """Mutation: render the resource ARN, which is what the tagging API hands back.

    Every ARN carries the account id, and this report goes into a scheduled log and a step
    summary in a public repository. The identifier after the last colon is what
    `aws batch describe-jobs` takes anyway, so nothing is lost by rendering that instead.
    """
    board = build_board(
        wandb_runs=[],
        resources=[a_resource(UNNAMED_RUN)],
        outputs=[a_prefix(UUID4_PREFIX)],
        gaps=[a_gap()],
    )

    assert not ACCOUNT_LITERAL.search(render(board))

    source = (PROJECT_ROOT / "tools" / "visibility_board.py").read_text(encoding="utf-8")

    assert "carries the account id" in source
    assert not ACCOUNT_LITERAL.search(source)


# ----------------------------------------------------------------------------------------
# The grant the account side is read under, written down in two places
# ----------------------------------------------------------------------------------------


def test_the_statement_the_report_quotes_is_one_somebody_can_apply() -> None:
    """Mutation: describe the missing grant in prose instead of quoting it.

    The whole value of naming an IAM change in a report is that whoever applies it pastes a
    reviewed string rather than reconstructing one from a sentence at 05:00. A quoted
    statement that does not parse, or that names the wrong action, is worse than the sentence
    because it looks authoritative.
    """
    parsed = yaml.safe_load(MISSING_TAG_GRANT)

    assert isinstance(parsed, list) and len(parsed) == 1
    statement = parsed[0]
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "tag:GetResources"
    # The action takes no resource type, so a policy naming one denies the call outright. The
    # region condition is the only narrowing it admits and is the same one the role's other
    # unscopable grant carries.
    assert statement["Resource"] == "*"
    assert statement["Condition"] == {
        "StringEquals": {"aws:RequestedRegion": {"Fn::Sub": "${AWS::Region}"}}
    }
    assert "Sid" in statement


def test_the_statement_reaches_the_report_rather_than_only_the_source() -> None:
    """Mutation: hold the statement as a constant and describe it in the gap instead.

    A grant named in a comment in a Python file is a grant nobody applies. The gap section is
    what a reader sees at 05:00, so the statement is rendered into it in a fenced block, ready
    to paste under the policy in `infra/iam/audit-reader-role.yaml`.
    """
    report = render(build_board(wandb_runs=[], resources=None, outputs=[], gaps=[a_gap()]))

    assert MISSING_TAG_GRANT in report
    assert "```yaml" in report
    assert "which runs logged nothing" in report


# ----------------------------------------------------------------------------------------
# The second account-side source, and the horizon each one covers
# ----------------------------------------------------------------------------------------


def test_a_run_the_tagging_api_has_forgotten_is_still_on_the_account_side() -> None:
    """THE ONE THAT MATTERS. Mutation: read the account from the tags alone, as before.

    The tagging API reports a resource while the resource exists and Batch drops a finished
    job after about a week, so the account side shrinks on its own. Measured on 2026-08-04
    the platform held 136 intent records, the tags answered for 112 runs, and five of the 24
    the board could not see carry a binding -- which means Batch accepted them and they ran.
    A denominator that quietly contracts is worse than a small one, because every count over
    it reads as a trend.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN)],
        bindings=[a_binding(NAMED_RUN), a_binding(UNNAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
    )

    assert board.account_run_ids == frozenset({NAMED_RUN, UNNAMED_RUN})
    assert board.in_account_not_in_wandb == (UNNAMED_RUN,)
    assert UNNAMED_RUN in board.known_run_ids

    report = render(board)

    assert "## Spend nobody can see a loss curve for" in report
    assert "| binding |" in report, "the row says which source still remembers the run"


def test_the_two_account_sources_are_named_separately_in_the_table() -> None:
    """Mutation: merge the two into one mapping and print one column.

    They are opposite kinds of evidence. A run known only from a binding is one the tagging
    API has already forgotten, which is the case this source was added for; a run known only
    from the tags never went through admission, which is a different finding with a
    different owner. One column that said "the account" would hide both.
    """
    board = build_board(
        wandb_runs=[],
        resources=[a_resource(NAMED_RUN)],
        bindings=[a_binding(DIED_RUN)],
        outputs=[],
    )

    report = render(board)

    assert f"| `{NAMED_RUN}` " in report and "| tags |" in report
    assert f"| `{DIED_RUN}` " in report and "| binding |" in report


def test_an_unread_binding_prefix_narrows_the_horizon_and_keeps_the_account_side() -> None:
    """Mutation: fold `binding/` into the required sync, which is the shorter diff.

    `sync_bucket` raises on a refused prefix rather than skipping it, so one denial would
    take the cost figures, the result records and the W&B reconciliation with it -- which is
    exactly what `attempt/` did to the whole cost mapping for months. The reader role does
    not hold `binding/` yet, so that denial is tonight's expected answer rather than a
    hypothetical.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN), a_resource(UNNAMED_RUN)],
        bindings=None,
        outputs=[a_prefix(NAMED_RUN)],
        gaps=[
            SourceGap(
                source="the account, from `binding/` records",
                reason="binding_records_not_read",
                detail="could not read the prefix",
                unanswered=("which runs the tagging API has already forgotten",),
                remedy=MISSING_BINDING_GRANT,
            )
        ],
    )

    assert board.account_run_ids == frozenset({NAMED_RUN, UNNAMED_RUN})
    assert board.in_account_not_in_wandb == (UNNAMED_RUN,)

    report = render(board)

    assert MISSING_BINDING_GRANT in report
    assert "| the account, from `binding/` records | not read |" in report


def test_the_report_says_what_each_number_was_counted_over() -> None:
    """THE ONE THAT MATTERS. Mutation: add the source and leave the counts unexplained.

    Adding the bindings moves the account side without anything about the account having
    moved, so a reader holding yesterday's board beside today's reads five new runs. Two of
    these windows move on their own and none of them was on the page, which made every
    comparison across mornings a comparison of two populations.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN)],
        bindings=[a_binding(NAMED_RUN), a_binding(UNNAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
    )

    report = render(board)

    assert "## What these numbers are counted over" in report
    assert "| the account, from resource tags | 1 |" in report
    assert "| the account, from `binding/` records | 2 |" in report
    assert "roughly a week" in report, "the tagging API's window is the one that moves"
    assert (
        "The account side of every finding above is the union of the first two, which is 2"
        in report
    )


def test_the_horizon_is_printed_even_when_every_source_answered() -> None:
    """Mutation: print the windows only beside a gap, since a whole board needs no caveat.

    A whole board is exactly when the numbers get compared, and the tagging API's window is
    narrowing on a night when nothing was refused. The horizon is a property of the sources
    rather than a symptom of a failure.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=[a_resource(NAMED_RUN)],
        bindings=[a_binding(NAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        costs={},
    )

    assert not board.disagrees
    assert "## What these numbers are counted over" in render(board)


def test_the_binding_horizon_names_the_dates_it_actually_covers() -> None:
    """Mutation: claim "every run ever" and leave it there.

    It is true and unfalsifiable, and it is the one window here that can be measured. A
    reader who can see the oldest record behind the claim can tell a source that goes back
    to the first submission from one that was truncated by a partial sync.
    """
    board = build_board(
        wandb_runs=[],
        resources=[],
        bindings=[a_binding(NAMED_RUN, day=28), a_binding(UNNAMED_RUN, day=31)],
        outputs=[],
    )

    assert "2026-07-28 to 2026-07-31" in render(board)


def test_agreement_is_counted_over_the_union_of_the_account_sources() -> None:
    """Mutation: require both account sources before counting agreement.

    It reads as the careful choice and it makes the number `None` every night until an IAM
    change lands, which turns a count somebody skips past into a caveat they stop reading. A
    run either source names really did run, so a missing source can only under-count
    agreement -- unlike an unread W&B, which would invent findings. That asymmetry is the
    same one `in_wandb_with_no_output` and `output_with_no_wandb_run` are written around.
    """
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=None,
        bindings=[a_binding(NAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        gaps=[a_gap()],
    )

    assert board.agreeing == 1
    assert board.in_account_not_in_wandb == ()


def test_neither_account_source_read_still_answers_nothing() -> None:
    """The line the test above must not cross: no account source is still no account side."""
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED)],
        resources=None,
        bindings=None,
        outputs=[a_prefix(NAMED_RUN)],
        gaps=[a_gap()],
    )

    assert board.account_run_ids is None
    assert board.in_account_not_in_wandb is None
    assert board.agreeing is None


def test_a_binding_the_contract_refuses_still_names_its_run(tmp_path: Path) -> None:
    """THE ONE THAT MATTERS. Mutation: skip a record `BatchJobBinding` will not validate.

    Three of the committed bindings are refused: an early state machine wrote the whole
    execution payload where `array_size` takes an integer. Those runs ran, the records are
    immutable, and dropping them would take three runs out of the denominator over a field
    that says nothing about which run it is -- which is the defect this source exists to
    close, reintroduced by the reader that closes it.
    """
    broken = a_binding_record(DIED_RUN)
    broken["array_size"] = {"the whole": "execution payload"}
    root = a_binding_tree(
        tmp_path, {NAMED_RUN: a_binding_record(NAMED_RUN), DIED_RUN: broken}
    )

    bound, degraded = read_binding_records(root)

    assert {entry.run_id for entry in bound} == {NAMED_RUN, DIED_RUN}
    assert degraded == 1
    assert next(entry for entry in bound if entry.run_id == DIED_RUN).compute_profile is None
    assert next(entry for entry in bound if entry.run_id == NAMED_RUN).compute_profile


def test_a_binding_with_no_readable_run_id_is_counted_and_not_invented(tmp_path: Path) -> None:
    """Mutation: fall back to the file name, which is the run id for every record there is.

    It is, and taking it from there would mean this reader trusts a key rather than a
    record. A binding carrying no run id anybody can read is a record the state machine
    should not have been able to write, and inventing one out of the object key would hide
    exactly that.
    """
    root = a_binding_tree(tmp_path, {NAMED_RUN: {"schema_version": 1, "run_id": "not-a-run"}})

    bound, degraded = read_binding_records(root)

    assert bound == ()
    assert degraded == 1


def test_a_binding_stored_as_a_string_holding_json_is_still_read(tmp_path: Path) -> None:
    """The state machine writes canonical bytes rather than re-encoding, so both shapes exist."""
    root = a_binding_tree(
        tmp_path, {NAMED_RUN: json.dumps(json.dumps(a_binding_record(NAMED_RUN)))}
    )

    bound, degraded = read_binding_records(root)

    assert [entry.run_id for entry in bound] == [NAMED_RUN]
    assert degraded == 0


def test_a_binding_tree_that_is_not_there_raises_rather_than_reading_as_no_runs(
    tmp_path: Path,
) -> None:
    """THE ONE THAT MATTERS. Mutation: answer with no runs when the directory is absent.

    An empty account side is a claim -- it says this platform has never started anything --
    and a prefix nobody synced is not. Collapsing them would make a laptop pointed at a
    partial tree report every W&B run and every output prefix as belonging to a run that
    never ran, which is the failure `read_tagged_resources` already refuses on its own side.
    """
    from report_run_costs import ReportInputError

    with pytest.raises(ReportInputError):
        read_binding_records(tmp_path)


def test_the_degrading_prefix_is_not_one_the_board_cannot_run_without() -> None:
    """Mutation: declare `binding/` in both lists, so the sync asks for it twice.

    The two sets mean opposite things to `tests/test_audit_workflow.py`: everything in the
    required set has to be granted, and everything in the degrading set is allowed to be and
    is not required to be. A prefix in both would be asserted to be granted and permitted
    not to be, which is a check that cannot fail.
    """
    assert set(REQUIRED_LINEAGE_PREFIXES) & set(DEGRADING_LINEAGE_PREFIXES) == set()
    assert BINDING_PREFIX in DEGRADING_LINEAGE_PREFIXES
    assert "result" in REQUIRED_LINEAGE_PREFIXES, (
        "the W&B reconciliation reads the result records and cannot run without them"
    )


def test_the_binding_statement_the_report_quotes_is_one_somebody_can_apply() -> None:
    """Mutation: describe the missing grant in prose instead of quoting it.

    The value of naming an IAM change in a 05:00 report is that whoever applies it pastes a
    reviewed string rather than reconstructing one from a sentence, which is the argument
    `MISSING_TAG_GRANT` is already held to. Only the statement that can be pasted whole is
    quoted; the other half of the change edits an existing statement and is said in words.
    """
    parsed = yaml.safe_load(MISSING_BINDING_GRANT)

    assert isinstance(parsed, list) and len(parsed) == 1
    statement = parsed[0]
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "s3:GetObject"
    assert statement["Resource"]["Fn::Sub"].endswith(f"-lineage/{BINDING_PREFIX}/*")
    assert "Sid" in statement
    assert not ACCOUNT_LITERAL.search(MISSING_BINDING_GRANT)


# ----------------------------------------------------------------------------------------
# The W&B reference reconciliation, which is reported and does not move the exit code
# ----------------------------------------------------------------------------------------


def a_reading(*run_ids: str) -> Any:
    from wandb_reconciliation import ReferenceReading

    return ReferenceReading(
        references=tuple(
            WandbReference(
                run_id=run_id,
                entity="eduLLM",
                project="eduLLM",
                name=run_id,
                outcome="succeeded",
            )
            for run_id in run_ids
        ),
        results_read=len(run_ids),
        without_reference=0,
        unparsed=0,
    )


def test_a_record_naming_a_run_wandb_does_not_have_is_reported() -> None:
    reading = a_reading(NAMED_RUN)
    board = build_board(
        wandb_runs=[],
        resources=[a_resource(NAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        observations=observe(reading.references, []),
        reference_reading=reading,
    )

    assert len(board.false_references) == 1

    report = render(board)

    assert "## Whether the lineage records name W&B runs that exist" in report
    assert "lineage record(s) name a W&B run that does not exist" in report


def test_a_false_reference_does_not_turn_a_clean_board_into_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: add the false references to `disagrees`.

    It is a real disagreement and it is one nobody can repair: the lineage bucket refuses
    any write to a key that already exists, so the 28 records carrying a false reference
    carry it for ever. Gating on it would hold this job red permanently, and the next real
    finding would arrive at a job that was already red -- which is the argument
    `tools/find_runs_that_saved_nothing.py` makes beside its own acknowledgement list. The
    count is in the verdict line and the runs are in a table, which is what a reader acts on.
    """
    reading = a_reading(NAMED_RUN)
    board = build_board(
        wandb_runs=[a_wandb_run(NAMED_RUN, Match.NAMED, project="somewhere-else")],
        resources=[a_resource(NAMED_RUN)],
        bindings=[a_binding(NAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        costs={},
        observations=observe(
            reading.references, [a_wandb_run(NAMED_RUN, Match.NAMED, project="somewhere-else")]
        ),
        reference_reading=reading,
    )

    assert board.false_references, "the reference is false and the board says so"
    assert not board.disagrees
    assert _exit_for(board, monkeypatch, tmp_path) == EXIT_OK


def test_the_reconciliation_does_not_rescue_a_board_that_disagrees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other direction, which is the one the exit code must not lose.

    The board exits 1 today because it found 78 real disagreements, and that is the tool
    working. Nothing added here is allowed to move a run off the account side or otherwise
    turn one of those into a pass.
    """
    reading = a_reading(NAMED_RUN)
    resolves = a_wandb_run(NAMED_RUN, Match.NAMED, project="eduLLM")
    board = build_board(
        wandb_runs=[resolves],
        resources=[a_resource(NAMED_RUN)],
        bindings=[a_binding(NAMED_RUN), a_binding(UNNAMED_RUN)],
        outputs=[a_prefix(NAMED_RUN)],
        observations=observe(reading.references, [resolves]),
        reference_reading=reading,
    )

    assert board.false_references == ()
    assert board.disagrees
    assert _exit_for(board, monkeypatch, tmp_path) == EXIT_DISAGREES


def test_an_unreachable_wandb_marks_no_lineage_record_false() -> None:
    """THE ONE THAT MATTERS, again, at the board's own boundary.

    With W&B unread every reference is trivially unresolvable, and a board that printed that
    would report the whole result store as lying on the morning a key lapsed.
    """
    reading = a_reading(NAMED_RUN, UNNAMED_RUN)
    board = build_board(
        wandb_runs=None,
        resources=[a_resource(NAMED_RUN)],
        outputs=[],
        observations=observe(reading.references, None),
        reference_reading=reading,
        gaps=[a_gap()],
    )

    assert board.false_references == ()

    report = render(board)

    assert "Not asked" in report
    assert "name a W&B run that does not exist" not in report


def test_the_runs_that_logged_nowhere_are_named_on_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: print them only into the markdown.

    A step summary is read by a person the morning after; a machine-readable line is what
    lets somebody grep a month of scheduled logs for a run that never logged. It costs one
    line and it is the reporting half of this change, which is the part that was asked for.
    """
    reading = a_reading(NAMED_RUN)
    board = build_board(
        wandb_runs=[],
        resources=[a_resource(NAMED_RUN)],
        outputs=[],
        observations=observe(reading.references, []),
        reference_reading=reading,
    )

    _exit_for(board, monkeypatch, tmp_path)

    assert f"logged_nowhere {NAMED_RUN}" in capsys.readouterr().err


def test_the_machine_readable_answer_is_written_when_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import visibility_board

    reading = a_reading(NAMED_RUN)
    board = build_board(
        wandb_runs=[],
        resources=[a_resource(NAMED_RUN)],
        outputs=[],
        observations=observe(reading.references, []),
        reference_reading=reading,
    )
    monkeypatch.setattr(visibility_board, "_collect", lambda _: board)
    landing = tmp_path / "observations.json"

    visibility_board.main(
        ["--output", str(tmp_path / "board.md"), "--wandb-observations", str(landing)]
    )

    written = json.loads(landing.read_text(encoding="utf-8"))

    assert written["counts"] == {"present": 0, "absent": 1, "unreachable": 0}
    assert [entry["run_id"] for entry in written["observations"]] == [NAMED_RUN]


def test_the_audit_reader_role_holds_the_statement_this_report_quotes() -> None:
    """Mutation: change one of the two spellings of the grant and leave the other.

    This test used to assert the opposite. It was the tripwire for the morning the IAM
    change landed, which was 2026-08-04, and inverting it is what that morning was for: the
    role now holds ``tag:GetResources`` and the account side is read rather than reported as
    a gap.

    WHAT REPLACES IT IS NOT NOTHING, BECAUSE THE STATEMENT IS STILL WRITTEN DOWN TWICE. The
    report prints ``MISSING_TAG_GRANT`` as the thing to paste when the read is refused, and
    the template carries the statement the read actually happens under. A refusal now means
    the deployed role drifted or the credential lapsed, and the first of those is repaired by
    pasting the report's version -- so the two have to be the same string, or whoever pastes
    it changes the role into something no test covers. Compared as parsed YAML rather than as
    text, since indentation differs between a quoted block and a template and neither
    spelling is more correct.

    THE LAST ASSERTION USED TO FORBID THE WHOLE ``batch:`` PREFIX AND NOW PINS IT TO TWO
    ACTIONS. The role holds ``batch:ListJobs`` and ``batch:DescribeJobs`` for the placement
    check that recomputes ``config/capacity.yaml``'s ``places`` column, so the old sentence
    -- that there is no substitute read at all -- stopped being true. Its point did not: a
    queue read is still not a substitute for the tagging grant, because it can only see the
    queues this platform created, and the gap this board reports is everything else the
    account ran. Written as an exact set rather than deleted, because what the old assertion
    was really guarding is that nobody drops the tagging grant on the strength of a batch
    read, and that guard is worth keeping pointed at the two actions that now exist.
    """
    quoted = yaml.safe_load(MISSING_TAG_GRANT)[0]
    statements = [
        statement
        for properties in load_template(ROLE_PATH)["Resources"].values()
        for policy in properties["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]
    granted = {
        action
        for statement in statements
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert "tag:GetResources" in granted, (
        "the role no longer holds the tagging read, so the account side of this board is "
        "unreadable and every night is an exit 2. Re-apply "
        "infra/iam/audit-reader-role.yaml from a laptop."
    )
    assert [statement for statement in statements if statement == quoted] == [quoted], (
        "the template's tagging statement is not the one the report quotes. Whoever pastes "
        "MISSING_TAG_GRANT out of a 05:00 report would change the role to something no test "
        "covers."
    )
    assert {action for action in granted if action.startswith("batch:")} == {
        "batch:ListJobs",
        "batch:DescribeJobs",
    }, (
        "the queue reads are not a substitute for the tagging grant, which is what the "
        "assertion above keeps here. ListJobs enumerates a named queue, so between them the "
        "two see what this platform submitted to the sixteen queues it created and nothing "
        "the account ran anywhere else -- and the account side is the half of the comparison "
        "this board exists to supply. Asserted as an exact set rather than as an absence, so "
        "a third batch action is argued for where the role's grants are argued for."
    )
