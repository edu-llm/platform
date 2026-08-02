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

The IAM statement the board needs and the role does not hold is asserted here as well, because
the value of quoting a statement in a report is that somebody can apply it, and a statement
that does not parse is prose.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from infrastructure_support import ACCOUNT_LITERAL, IAM_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from visibility_board import (
    EXIT_DISAGREES,
    EXIT_OK,
    EXIT_UNUSABLE,
    MISSING_TAG_GRANT,
    PLATFORM_TAG_KEYS,
    RUN_ID_TAG,
    Board,
    Match,
    OutputPrefix,
    SourceGap,
    TaggedResource,
    WandbRun,
    build_board,
    read_output_prefixes,
    read_tagged_resources,
    render,
    run_id_of,
    team_runs_prefix,
)

from edullm_platform.capture_tooling import CaptureFailedError
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix

EXECUTION = PROJECT_ROOT / "src" / "edullm_platform" / "execution.py"
ROLE_PATH = IAM_ROOT / "nightly-reader-role.yaml"

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

    It is the obvious simplification and it is denied. The nightly reader role conditions
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
# The grant the role does not hold
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
    to paste under the policy in `infra/iam/nightly-reader-role.yaml`.
    """
    report = render(build_board(wandb_runs=[], resources=None, outputs=[], gaps=[a_gap()]))

    assert MISSING_TAG_GRANT in report
    assert "```yaml" in report
    assert "which runs logged nothing" in report


def test_the_nightly_reader_role_still_does_not_hold_that_grant() -> None:
    """Mutation: leave the gap reported after somebody has closed it.

    A report that goes on asking for a grant the account already has sends every reader to
    apply a stack that is already applied. This is the assertion that fails on the morning
    after the IAM change lands, which is when the degradation path and the sentence describing
    it both stop being true and have to be removed.
    """
    granted = {
        action
        for properties in load_template(ROLE_PATH)["Resources"].values()
        for policy in properties["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert "tag:GetResources" not in granted, (
        "the role now holds the tagging read, so the account source is no longer a gap. "
        "Remove MISSING_TAG_GRANT and the degradation path in tools/visibility_board.py, and "
        "delete this test with them."
    )
    assert not any(action.startswith("batch:") for action in granted), (
        "there is still no substitute read, which is what makes the tagging grant the "
        "only way to see what the account is running"
    )
