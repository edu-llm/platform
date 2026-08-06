"""Hold the submission watcher to going red on a defect and staying green on a judgement.

The watcher exists because ``submit-run.yml`` failed seven times on 2026-08-06 between
05:33 and 06:50 UTC and nothing noticed. Its whole difficulty is that six of those seven and
the one that was *correct* all arrived as the same thing: a failed run, whose failing job was
the compile job, whose conclusion was a refusal. A watcher that reports all of them is a
watcher somebody mutes in a week, and a muted watcher is what the platform already had.

So both directions are pinned here, and the fixtures are the real logs rather than invented
ones. ``QUOTING_LOG`` is what run 31075087268 printed and ``NO_IMAGE_LOG`` is what run
31074877032 printed, both trimmed to the lines that decide the classification and both
keeping the echoed script, because the echoed script is what a first attempt got wrong.

**The same mistake was then made again one layer up, and the gate cases below are the fix.**
The watcher's first real look, over 09:34 to 11:06 on the day it shipped, reported three
platform defects. There were none. Two of the three were runs a lead had deliberately
declined -- GitHub gives a rejected deployment review the same ``failure`` conclusion it
gives a crash, and skips admission either way -- and the third was a gate environment
refusing a feature branch. The ``Gate`` fixtures here are what those runs actually carried on
``/approvals`` and on their annotations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import only_job, run_step_script, step, write_stub

from tools.watch_submission_health import (
    EXIT_DISAGREES,
    EXIT_OK,
    GATE_JOB,
    Failure,
    Gate,
    classify_failure,
    decide,
    normalise_reason,
    reason_of,
    spoken,
)

COMPILE_JOB = "Compile the submission and classify it"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "watch-submission-health.yml"


def log_line(content: str, *, echoed: bool = False) -> str:
    """One line as ``gh run view --log-failed`` writes it.

    ``echoed`` wraps the content the way GitHub wraps a script line it is echoing back
    before running it, which is the difference between a step's source and its output.
    """
    body = f"\x1b[36;1m{content}\x1b[0m" if echoed else content
    return f"{COMPILE_JOB}\tCompile the submission\t2026-08-06T05:46:14.3966187Z {body}"


#: Every compile failure's log carries both of the step's classifications as the body of an
#: ``echo``, whichever one it actually reached. This is the trap, and it is in both fixtures.
ECHOED_CLASSIFICATIONS = (
    log_line('  echo "submission_refused" >&2', echoed=True),
    log_line('  echo "submission_form_unusable" >&2', echoed=True),
    log_line(
        '  echo "The form or the reviewed configuration could not be read.'
        ' This is not a refusal on the merits." >&2',
        echoed=True,
    ),
)

QUOTING_LOG = "\n".join(
    (
        *ECHOED_CLASSIFICATIONS,
        log_line("##[endgroup]"),
        log_line("the submission does not compile into a valid manifest: 1 validation error"),
        log_line("command"),
        log_line(
            "  Value error, bash -lc reads exactly one word as the command, and this"
            " submission gives it 3. It would run `python` alone and hand the rest to it as"
            " $0, $1, $2 -- which starts, costs an instance, and exits without running your"
            " program. Quote the whole program: bash -lc 'python .edullm/time_attention.py"
            " $EDULLM_RUN_ID' [type=value_error, input_value=('bash', '-lc', 'python',...),"
            " input_type=tuple]"
        ),
        log_line("submission_refused"),
        log_line("##[error]Process completed with exit code 1."),
    )
)

#: The same defect, a different submission. Fourteen words rather than three and a different
#: program quoted back, so the tail differs and only the first sentence is shared.
QUOTING_LOG_OTHER = QUOTING_LOG.replace("gives it 3.", "gives it 14.").replace(
    "time_attention.py", "train_on_corpus.py"
)

NO_IMAGE_LOG = "\n".join(
    (
        *ECHOED_CLASSIFICATIONS,
        log_line("##[endgroup]"),
        log_line(
            "submission refused: commit 9ea6d144f89c6b274abda834ba92555889cad66f has no"
            " image published from it, so there is nothing for this submission to run."
            " Build the commit before submitting it."
        ),
        log_line("submission_refused"),
        log_line("##[error]Process completed with exit code 1."),
    )
)

UNUSABLE_LOG = "\n".join(
    (
        *ECHOED_CLASSIFICATIONS,
        log_line("##[endgroup]"),
        log_line("Traceback (most recent call last):"),
        log_line("submission_form_unusable"),
        log_line("##[error]Process completed with exit code 1."),
    )
)


def failure_from(log: str, *, title: str, run_id: int = 1) -> Failure:
    return classify_failure(
        run_id=run_id,
        title=title,
        created_at="2026-08-06T05:45:32Z",
        failing_jobs=[COMPILE_JOB],
        log=log,
    )


def test_the_echoed_script_is_not_read_as_something_the_step_said() -> None:
    """The trap, pinned as its own case because a first attempt fell straight into it.

    GitHub echoes a step's script into that step's log, so ``submission_form_unusable`` is
    present in the log of every compile failure as the body of an ``echo`` that may never
    have run. Reading the log as a substring reported all eight failures in the window as
    platform faults, including the one that was the platform working correctly.
    """
    assert "submission_form_unusable" in NO_IMAGE_LOG
    said = spoken(NO_IMAGE_LOG)
    assert "submission_form_unusable" not in said
    assert "submission_refused" in said


def test_a_commit_with_no_image_is_the_platform_working() -> None:
    refusal = failure_from(NO_IMAGE_LOG, title="OLMo-core by someone / attention-timing")
    assert refusal.kind == "refusal"
    assert "no image published" in refusal.reason


def test_a_form_the_compiler_could_not_read_is_never_a_judgement() -> None:
    fault = failure_from(UNUSABLE_LOG, title="OLMo-core by someone / attention-timing")
    assert fault.kind == "platform_fault"


def test_a_job_after_the_compile_job_is_never_a_judgement() -> None:
    """A run can fail with a clean compile, and then no marker is present to read.

    Asked in the wrong order this lands as unreadable, which would leave an infrastructure
    failure looking like a log GitHub had expired.
    """
    fault = classify_failure(
        run_id=2,
        title="OLMo-core by someone / attention-timing",
        created_at="2026-08-06T05:45:32Z",
        failing_jobs=["Read which image the declared commit published"],
        log=None,
    )
    assert fault.kind == "platform_fault"
    assert "image" in fault.reason


def branch_barrier(branch: str, environment: str) -> str:
    """The annotation GitHub leaves when a branch may not deploy to a gate environment.

    Verbatim from run 31094767578, and the only annotation on that job that named a cause.
    The other one it carried is the generic sentence a decline carries too.
    """
    return (
        f'Branch "{branch}" is not allowed to deploy to {environment} due to environment'
        " protection rules."
    )


def at_the_gate(gate: Gate | None, *, title: str = "a", run_id: int = 1) -> Failure:
    """A run whose only failed job is the gate job, which is how all four of these arrive."""
    return classify_failure(
        run_id=run_id,
        title=title,
        created_at="2026-08-06T11:06:13Z",
        failing_jobs=[GATE_JOB],
        log=None,
        gate=gate,
    )


def test_a_lead_declining_a_run_is_not_the_platform_failing() -> None:
    """RUN 31095905306, AND THE REASON THIS FILE CHANGED.

    GitHub gives a rejected deployment review the same ``failure`` conclusion it gives a job
    that crashed, and skips admission either way. The watcher's first look reported two runs
    a person had deliberately declined as platform defects. A lead refusing a $781 run is the
    gate working, and going red on it is how the whole signal gets muted.
    """
    declined = at_the_gate(Gate(started=False, review="rejected", reviewer="philote-dev", said=True))
    assert declined.kind == "declined"
    assert "philote-dev declined it" in declined.reason


def test_a_decline_does_not_quote_back_what_the_reviewer_typed() -> None:
    """The only free text a person types that could reach a public step summary.

    Who declined it and whether they explained is the whole signal a health check needs. The
    sentence itself lives on the run page and in the notifier message, which is where a
    submitter is sent for it.
    """
    with_reason = at_the_gate(Gate(started=False, review="rejected", reviewer="a-lead", said=True))
    without = at_the_gate(Gate(started=False, review="rejected", reviewer="a-lead", said=False))
    assert with_reason.reason == "a-lead declined it at the gate, and the reason is on the run page"
    assert without.reason == "a-lead declined it at the gate, and no reason was given"


def test_the_gate_releasing_a_run_that_then_broke_is_still_a_fault() -> None:
    """The boundary in the other direction, which the decline must not be allowed to cover.

    A declined gate job never runs a step. One that ran steps was released by somebody and
    then failed inside admission, and that is infrastructure however the run is labelled.
    """
    fault = at_the_gate(Gate(started=True))
    assert fault.kind == "platform_fault"
    assert "released" in fault.reason


def test_a_branch_policy_refusing_a_dispatch_is_a_refusal_and_not_an_outage() -> None:
    """RUN 31094767578, which was neither a decline nor a defect.

    A feature branch dispatched at a gate environment restricted to ``main``. The annotation
    names its own cause, which makes it a deterministic refusal like an unregistered dataset,
    so it goes through the repetition test rather than straight to red.
    """
    refusal = at_the_gate(
        Gate(
            started=False,
            barriers=(branch_barrier("edullm/say-which-install-submitted", "run-approval-preview"),),
        )
    )
    assert refusal.kind == "refusal"
    assert decide([refusal], considered=1, succeeded=0).healthy


def test_a_branch_policy_that_has_broken_for_everybody_does_go_red() -> None:
    """The same rule read the other way, which is why the refusal is not simply ignored.

    A gate environment misconfigured so that ``main`` cannot deploy refuses every submission
    with one identical sentence, and three of those across two submissions is systemic.
    """
    barrier = (branch_barrier("main", "run-approval-lead"),)
    report = decide(
        [
            at_the_gate(Gate(started=False, barriers=barrier), title=title, run_id=index)
            for index, title in enumerate(("a", "b", "c"))
        ],
        considered=3,
        succeeded=0,
    )
    assert not report.healthy
    assert len(report.systemic) == 1


def test_a_run_nobody_ever_reviewed_is_its_own_thing_and_is_red() -> None:
    """The third case, and the one the current outage makes most likely.

    No review and no rule that named itself: the request reached a gate and the run ended
    without a person. That is not a defect in any code and is not a judgement either, so it
    is reported as itself -- but it is red, because it means the ask never got to anybody.
    """
    unreviewed = at_the_gate(Gate(started=False, barriers=()))
    assert unreviewed.kind == "unreviewed"
    report = decide([unreviewed], considered=1, succeeded=0)
    assert not report.healthy
    assert len(report.unreviewed) == 1
    assert not report.faults


def test_a_gate_that_could_not_be_asked_is_neither_counted_nor_dismissed() -> None:
    """Both ways of failing to read it, held to the same stance as an expired log.

    Guessing red on an API hiccup is crying wolf and guessing green is being blind, and the
    file already has a third answer for exactly this.
    """
    assert at_the_gate(None).kind == "unreadable"
    assert at_the_gate(Gate(started=False, barriers=None)).kind == "unreadable"


def test_a_window_of_nothing_but_declines_is_a_green_window() -> None:
    """What tomorrow looks like when the gate is used for its purpose.

    Five leads saying no to five expensive runs is five ``failure`` conclusions and a
    perfectly healthy platform. This is the case that decides whether anybody still reads
    the watcher in a month.
    """
    report = decide(
        [
            at_the_gate(
                Gate(started=False, review="rejected", reviewer="a-lead", said=True),
                title=f"OLMo-core by researcher-{index} / sweep",
                run_id=index,
            )
            for index in range(5)
        ],
        considered=5,
        succeeded=0,
    )
    assert report.healthy
    assert len(report.declined) == 5
    assert not report.faults
    assert not report.systemic


def test_the_night_the_watcher_shipped_holds_no_platform_defect() -> None:
    """THE FIVE RUNS OF 2026-08-06 THAT THE FIRST LOOK CALLED THREE DEFECTS. There were none.

    Two runs a lead declined at the lead gate (31094100261 and 31095905306), two compile
    refusals of one badly quoted command (31094741699 and 31094757003), and one feature
    branch dispatched at a gate environment restricted to ``main`` (31094767578). Every
    classification here is what the run actually carried on ``/approvals``, on its jobs and
    on its annotations. The ids are in this sentence rather than in the calls below, because
    a tracked-tree guard rejects integer literals of that width anywhere in the repository.

    **It is also the answer to whether agent traffic should be counted at all.** Every
    dispatch in this window was one account on a feature branch, and tomorrow the population
    is thirty-five people on ``main``, so a watcher tuned on it could easily be tuned wrong.
    It needs no rule about who dispatched what: the distinct-submission threshold already
    holds the two quoting refusals at two runs over one submission, because an agent
    retrying one experiment carries one display title. A rule excluding this account would
    have excluded all seven of the failures the watcher was built for, which were the same
    account.
    """
    report = decide(
        [
            at_the_gate(
                Gate(started=False, review="rejected", reviewer="philote-dev", said=True),
                title="OLMo-core by philote-dev / the-envelope-proof",
                run_id=1,
            ),
            failure_from(
                QUOTING_LOG,
                title="OLMo-core by philote-dev / which-install-submitted",
                run_id=2,
            ),
            failure_from(
                QUOTING_LOG_OTHER,
                title="OLMo-core by philote-dev / which-install-submitted",
                run_id=3,
            ),
            at_the_gate(
                Gate(
                    started=False,
                    barriers=(
                        branch_barrier(
                            "edullm/say-which-install-submitted", "run-approval-preview"
                        ),
                    ),
                ),
                title="OLMo-core by philote-dev / which-install-submitted",
                run_id=4,
            ),
            at_the_gate(
                Gate(started=False, review="rejected", reviewer="philote-dev", said=True),
                title="OLMo-core by philote-dev / envelope-after-release",
                run_id=5,
            ),
        ],
        considered=8,
        succeeded=3,
    )
    assert report.healthy
    assert not report.faults
    assert not report.systemic
    assert not report.unreviewed
    assert len(report.declined) == 2
    assert len(report.tolerated) == 3


def test_a_real_outage_is_still_caught_in_a_window_full_of_declines() -> None:
    """The direction that matters more, pinned beside the one that prompted the change.

    Quietening the declines must not quieten anything beside them, so the seven-failure
    window is replayed with four declines interleaved into it.
    """
    failures = [
        *(
            failure_from(QUOTING_LOG, title=f"OLMo-core by philote-dev / {name}", run_id=index)
            for index, name in enumerate(("onboarding", "approval-test", "day-one-walk"))
        ),
        *(
            at_the_gate(
                Gate(started=False, review="rejected", reviewer="a-lead", said=True),
                title=f"OLMo-core by a-lead / expensive-{index}",
                run_id=100 + index,
            )
            for index in range(4)
        ),
    ]
    report = decide(failures, considered=12, succeeded=5)
    assert not report.healthy
    assert len(report.systemic) == 1
    assert len(report.declined) == 4


def test_a_log_github_has_expired_is_neither_counted_nor_dismissed() -> None:
    unknown = classify_failure(
        run_id=3,
        title="OLMo-core by someone / attention-timing",
        created_at="2026-08-06T05:45:32Z",
        failing_jobs=[COMPILE_JOB],
        log=None,
    )
    assert unknown.kind == "unreadable"


def test_one_defect_refusing_two_different_commands_has_one_signature() -> None:
    """Three words and fourteen words are one defect, and must reach one threshold.

    The sentence the compiler writes names the word count it objected to and then quotes the
    submitter's own program back. Keeping the tail gives two instances of one defect two
    signatures, and neither would ever repeat often enough to be reported.
    """
    three = failure_from(QUOTING_LOG, title="a")
    fourteen = failure_from(QUOTING_LOG_OTHER, title="b")
    assert three.reason == fourteen.reason
    assert three.reason == (
        "bash -lc reads exactly one word as the command, and this submission gives it <n>"
    )


def test_a_commit_sha_does_not_survive_into_a_signature() -> None:
    """Two people refused for the same reason about different commits are one reason.

    This is also the only place a value from a run reaches a public step summary, so the
    normalisation that groups them is the same one that keeps an account id out.
    """
    reason = normalise_reason(
        "commit 9ea6d144f89c6b274abda834ba92555889cad66f has no image published from it."
    )
    assert "9ea6d144" not in reason
    assert reason == "commit <hex> has no image published from it"


def test_the_validation_sentence_is_preferred_over_the_envelope() -> None:
    assert reason_of(spoken(QUOTING_LOG)) is not None
    assert reason_of(spoken(QUOTING_LOG)).startswith("bash -lc reads exactly one word")  # type: ignore[union-attr]


def test_the_seven_that_nobody_noticed_are_reported() -> None:
    """The window this whole file exists for, as the watcher sees it.

    Six distinct submissions refused by one defect. This is the case that must go red, and it
    must go red naming the defect rather than naming six researchers.
    """
    failures = [
        failure_from(QUOTING_LOG, title=title, run_id=index)
        for index, title in enumerate(
            (
                "OLMo-core by philote-dev / onboarding",
                "OLMo-core by philote-dev / attention-timing",
                "OLMo-core by philote-dev / attention-timing",
                "OLMo-core by philote-dev / approval-test",
                "OLMo-core by philote-dev / day-one-walk",
                "OLMo-core by philote-dev / probe-attention-timing",
            )
        )
    ]
    report = decide(failures, considered=13, succeeded=6)
    assert not report.healthy
    assert len(report.systemic) == 1
    reason, sharing = report.systemic[0]
    assert "bash -lc reads exactly one word" in reason
    assert len(sharing) == 6
    assert not report.tolerated


def test_a_correct_refusal_on_its_own_is_not_an_outage() -> None:
    """Number two of the seven, which was the platform doing its job.

    A window whose only run is this one is a hundred per cent failure and still green, because
    the alternative is a watcher that goes red every time somebody submits before their image
    is built.
    """
    report = decide(
        [failure_from(NO_IMAGE_LOG, title="OLMo-core by philote-dev / attention-timing")],
        considered=1,
        succeeded=0,
    )
    assert report.healthy
    assert len(report.tolerated) == 1
    assert not report.systemic


def test_the_correct_refusal_is_not_swept_up_by_the_defect_beside_it() -> None:
    """Both at once, which is what the real window held.

    The seven and the one arrived interleaved, so separating them in isolation proves less
    than separating them together.
    """
    failures = [
        *(
            failure_from(QUOTING_LOG, title=f"OLMo-core by philote-dev / {name}", run_id=index)
            for index, name in enumerate(("onboarding", "approval-test", "day-one-walk"))
        ),
        failure_from(NO_IMAGE_LOG, title="OLMo-core by philote-dev / attention-timing", run_id=9),
    ]
    report = decide(failures, considered=16, succeeded=8)
    assert not report.healthy
    assert len(report.systemic) == 1
    assert [one.kind for one in report.tolerated] == ["refusal"]
    assert "no image published" in report.tolerated[0].reason


def test_one_person_retrying_one_submission_is_not_systemic() -> None:
    """Three refusals under one display title are one person's afternoon.

    Runs alone would make this red at the third attempt, which is why the threshold is runs
    *and* distinct submissions. Somebody fixing a dataset name by trial is not an outage.
    """
    failures = [
        failure_from(NO_IMAGE_LOG, title="OLMo-core by one-person / one-experiment", run_id=index)
        for index in range(4)
    ]
    report = decide(failures, considered=4, succeeded=0)
    assert report.healthy
    assert not report.systemic
    assert len(report.tolerated) == 4


def test_a_single_fault_is_enough_without_any_repetition() -> None:
    """The asymmetry between the two signals, which is deliberate.

    A refusal needs repetition before it accuses the platform. A form the compiler could not
    read accuses nobody and is never correct, so one is the threshold.
    """
    report = decide(
        [failure_from(UNUSABLE_LOG, title="OLMo-core by someone / anything")],
        considered=20,
        succeeded=19,
    )
    assert not report.healthy
    assert len(report.faults) == 1


def test_a_window_with_nothing_wrong_is_green() -> None:
    report = decide([], considered=5, succeeded=5)
    assert report.healthy


def test_the_two_verdicts_are_the_two_exit_codes() -> None:
    """Pinned because the workflow branches on the code and not on the prose."""
    assert EXIT_OK == 0
    assert EXIT_DISAGREES == 1


def workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_watcher_holds_no_aws_credential() -> None:
    """The reason it can watch this at all, and the reason it costs nothing to run.

    Anything that assumes a role needs ``id-token``, a trust policy pinning it to a path on
    ``main``, and a deploy to change. This reads GitHub about GitHub, so it needs none of that
    and cannot be the thing that breaks a deploy.
    """
    # Structurally rather than as a text search, because the header argues at length about
    # why no token is requested and a search for the words finds the argument.
    loaded = workflow()
    assert "id-token" not in loaded["permissions"]
    steps = [step for job in loaded["jobs"].values() for step in job["steps"]]
    assert not [step for step in steps if "aws-actions/" in str(step.get("uses", ""))]
    assert not [step for step in steps if "role-to-assume" in str(step.get("with", ""))]
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_the_watcher_asks_for_no_more_than_it_needs() -> None:
    """``checks: read`` is here because annotations are the Checks API and not the Actions one.

    It is what tells a branch policy that named itself from a gate nobody ever answered, and
    listing any permission sets every unlisted one to ``none``, so leaving it off is a 403
    rather than a default. Everything else stays read-only and no AWS grant appears at all.
    """
    permissions = workflow()["permissions"]
    assert permissions == {
        "contents": "read",
        "actions": "read",
        "checks": "read",
        "issues": "write",
    }


def test_the_watcher_runs_often_enough_to_see_the_window_it_was_written_for() -> None:
    """Seventy-seven minutes was the outage. A daily cron is what the audit already had."""
    # ``on`` is the YAML 1.1 boolean true, which is why this reads it by that key rather than
    # by the string it is spelled with.
    triggers = workflow()[True]
    assert triggers["schedule"] == [{"cron": "*/30 * * * *"}]
    assert "workflow_dispatch" in triggers


def test_the_watcher_looks_when_a_submission_finishes_and_not_only_on_a_clock() -> None:
    """The cron is not a delivery guarantee and this one demonstrably was not delivered.

    Shipped on ``*/30``, its whole history was a single run at 09:34; the 10:00, 10:30 and
    11:00 ticks never fired, over a window holding five submissions that ended badly. The
    event names ``submit-run.yml`` by its ``name:``, so the two must not drift apart.
    """
    triggers = workflow()[True]
    assert triggers["workflow_run"]["types"] == ["completed"]
    named = triggers["workflow_run"]["workflows"]
    submit = yaml.safe_load(
        (PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml").read_text(encoding="utf-8")
    )
    assert named == [submit["name"]]


def test_the_clock_is_kept_underneath_the_event() -> None:
    """An hour in which nobody could dispatch at all produces no event to be woken by.

    That is what a broken dispatch path looks like from here and it is worth catching, so the
    event is an addition to the cron rather than a replacement for it.
    """
    triggers = workflow()[True]
    assert {"workflow_run", "schedule"} <= set(triggers)


def test_the_watcher_can_be_pointed_at_a_window_by_hand() -> None:
    """How both directions were proved against the real seven, so it stays available."""
    inputs = workflow()[True]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"since", "until"}


def telling_somebody() -> str:
    """The body of the step that carries the verdict to a person."""
    return str(step(only_job(workflow()), "Tell somebody")["run"])


#: A ``gh`` that answers the way GitHub did on 2026-08-06 at 11:16:08Z: the issue is filed and
#: its URL comes back on stdout, and a caller that goes looking for that issue again finds
#: nothing, because the search index it would have to ask has not caught up yet. The empty
#: answer is the point of the stub and not an oversight in it.
GH_DURING_THE_INDEXING_LAG = """
case "${1:-} ${2:-}" in
  "issue list")
    exit 0
    ;;
  "issue create")
    echo "https://github.com/edu-llm/platform/issues/381"
    ;;
  "issue comment")
    reference="${3:-}"
    if [[ -z "${reference}" || "${reference}" == --* ]]; then
      echo "invalid issue format: \\"${reference}\\"" >&2
      exit 1
    fi
    printf '%s' "${reference}" > "${STUB_STATE}/commented-on.txt"
    ;;
  *)
    echo "the step ran a gh command this stub does not know about: $*" >&2
    exit 64
    ;;
esac
"""


@pytest.fixture(name="telling")
def _telling(tmp_path: Path) -> Any:
    """The step, its verdict file and a ``gh`` that is blind to what it has just filed."""
    (tmp_path / "verdict.txt").write_text("six submissions, one defect\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "gh", GH_DURING_THE_INDEXING_LAG)
    return run_step_script(
        telling_somebody(),
        cwd=tmp_path,
        env={
            "GH_TOKEN": "not-a-token",
            "TITLE": "Submissions are failing for a reason no submitter can fix",
            "STUB_STATE": str(state),
        },
        stub_bin=stub_bin,
    ), state


def test_the_verdict_reaches_the_issue_that_was_just_filed(telling: Any) -> None:
    """THE RUN THAT DID ITS JOB AND THEN REPORTED ITSELF BROKEN, REPLAYED.

    Run 31096537986 filed issue #381 at 11:16:08Z, printed its URL, and half a second later
    ended with ``invalid issue format: ""``. It had thrown the URL away and asked
    ``gh issue list --search`` for the number back, and search is an index GitHub populates
    asynchronously, so a half-second-old issue is not in it. The 11:54Z run found the same
    issue through the same search without difficulty, which is what dates the window.

    Retrying the search would have been a patch on the symptom. The reference is on stdout
    and is never worth looking up, so this pins the outcome rather than the lookup: the
    verdict lands on the issue this run filed, with a ``gh`` that cannot answer any question
    about it.
    """
    result, state = telling
    assert result.returncode == 0, result.stderr
    commented_on = (state / "commented-on.txt").read_text(encoding="utf-8")
    assert commented_on == "https://github.com/edu-llm/platform/issues/381"


def test_nothing_here_asks_the_search_index_about_an_issue(telling: Any) -> None:
    """The dedupe was reading that same index, which is the twin of the failure above.

    Nobody had hit it because it needs two runs inside the indexing window, and this workflow
    fires on every submission: two finishing a minute apart would both have seen an empty
    index and both filed, which is the duplicate storm the dedupe exists to prevent. The
    exact-title match that replaces it is also narrower than ``in:title``, which was a
    full-text search that would equally have matched an issue quoting this title back.
    """
    script = telling_somebody()
    assert "--search" not in script
    assert "select(.title == env.TITLE)" in script
