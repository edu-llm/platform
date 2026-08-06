"""Which of the four classes each way out of ``edullm`` belongs to.

**THE EXIT CODE IS THE ONLY PART OF THIS BINARY A SCRIPT CAN READ WITHOUT PARSING PROSE,
WHICH IS WHY IT GETS A FILE OF ITS OWN.** Every other test here asserts a sentence, and a
sentence is for a person. A retry loop, a Makefile and an agent all branch on the number,
and the number is published under ``writing-releases-and-docs.md`` the same way a flag name
is, so getting one wrong is a lie told to the one reader who cannot notice.

**AND THE SUBJECT IS READ OUT OF THE PARSER RATHER THAN WRITTEN DOWN HERE.** A table in this
file saying ``status`` exits 3 on an unreachable GitHub would be a second copy of the thing
under test, correct on the day it was typed, and silent about the tenth verb somebody adds.
So the verbs come from :func:`build_parser_and_verbs`, the arguments each one needs come out
of its own usage line, and what is asserted is an invariant rather than a list. ``if it
reached gh, it exited 3`` holds for a verb that does not exist yet, and it fails the moment
a new verb reaches gh and answers something else.

The three mappings this file was written to hold down were all shipped wrong at once.
``logs`` and ``status`` reported a reporting workflow's own failure as a refused submission,
on two verbs that refuse nothing. ``--hours nope`` exited 1 and ``--attempts nope`` exited 2,
which is one mistake under two codes. And an unreachable GitHub shared 2 with a mistyped
flag, so the first script anybody writes could not tell what was worth retrying.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
from pathlib import Path
from typing import Any

import pytest

import edullm_platform.cli.main as cli
from edullm_platform.cli.actions import ADMISSION_JOB, CANCEL_WORKFLOW, SUBMIT_WORKFLOW
from edullm_platform.cli.main import (
    BUILT_TODAY,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNREACHABLE,
    EXIT_UNUSABLE,
    NOT_BUILT_YET,
    build_parser_and_verbs,
)
from tests.cli_support import (
    FakeRunner,
    failed,
    git_answers,
    invoke,
    lane_answers,
    ok,
    studio_answers,
    write_spec,
)

RUN_ID = "run_019fd2a1-4e07-7a3c-9d55-1b2f8c0e6a41"

#: A value for a flag a verb takes and this needs filled in to get past the local checks.
#: Applied only where a verb's own usage line carries the flag, so this is read against the
#: parser rather than asserted to match it, and
#: :func:`test_every_flag_this_file_fills_in_is_a_flag_some_verb_still_takes` fails if a
#: name here stops existing rather than quietly ceasing to apply.
A_VALUE_FOR = {
    "--reason": "wrong corpus",
    "--experiment": "an-experiment",
    "--dataset": "none",
    "--detail": "what I have already tried",
    "--project": "mixlaw",
    "--compute": "gpu-1xt4",
}

#: Which value to give a verb whose positional takes ``choices``, where the choices do not
#: all lead to the same place. ``add`` is the only one today and it is not a close call: four
#: of its five kinds are refused locally and run no command at all, so a case about what a
#: Ctrl-C does or what an unreachable GitHub costs would be driving a verb that reaches
#: neither and would pass by never getting there. The kind that does the work is the kind to
#: drive. :func:`test_every_choice_this_file_fills_in_is_still_a_choice_that_verb_takes`
#: fails if one of these stops being a choice, rather than the fill quietly ceasing to apply.
A_CHOICE_FOR = {"add": "repository"}


def verbs() -> dict[str, argparse.ArgumentParser]:
    """Every verb the parser carries, which is the population every case below runs over."""
    return build_parser_and_verbs()[1]


def usage_of(verb: str) -> str:
    return verbs()[verb].format_usage()


def _is_a_choice_group(token: str) -> bool:
    """Whether argparse rendered this slot as the set of values it accepts."""
    return token.startswith("{") and token.endswith("}")


def a_value_of(verb: str, rest: list[str]) -> str:
    """A value for an option, taken from its own choices where argparse printed them."""
    metavar = rest[0] if rest else ""
    if _is_a_choice_group(metavar):
        return A_CHOICE_FOR.get(verb, metavar[1:-1].split(",")[0])
    return "a-value"


def choices_of(verb: str) -> set[str]:
    """Every value a ``choices`` positional on this verb accepts, as argparse renders them."""
    return {
        choice
        for group in re.findall(r"\{([a-z0-9,\-]+)\}", usage_of(verb))
        for choice in group.split(",")
    }


def argv_for(verb: str) -> list[str]:
    """Enough arguments to drive one verb, read out of the usage line it prints.

    ``format_usage`` is argparse's own rendering of what a verb takes, which is the surface
    :func:`~edullm_platform.cli.main._nearest_flag` already reads for the same reason: it
    cannot drift from the parser and it survives a Python release rearranging the internals.
    Optional groups come wrapped in brackets, so stripping the brackets leaves exactly the
    required options and the positionals, and a required flag added to a verb tomorrow is
    filled in here without anybody editing this file.

    Anything with ``choices`` renders its values as ``{a,b,c}``, and filling that with the
    same ``a-value`` every other slot gets is argparse's exit 2 rather than a drive of the
    verb. Those are filled from the choices themselves, which holds for a positional and for
    a required option alike, so a verb that grows either is driven rather than refused on the
    day it is added. :data:`A_CHOICE_FOR` overrides the pick where the choices do not all
    lead to the same place.
    """
    usage = usage_of(verb)
    required = usage.partition("]")[2] if "]" in usage else usage
    while "[" in required:
        required = re.sub(r"\[[^\[\]]*\]", " ", required)
    tokens = [token for token in required.split() if token not in {"usage:", "edullm", verb}]
    argv: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--"):
            argv += [token, A_VALUE_FOR.get(token, a_value_of(verb, tokens[index + 1 :]))]
            index += 2
            continue
        if _is_a_choice_group(token):
            argv.append(A_CHOICE_FOR.get(verb, token[1:-1].split(",")[0]))
            index += 1
            continue
        argv.append(RUN_ID if "run_id" in token.lower() else "a-value")
        index += 1
    for flag, value in A_VALUE_FOR.items():
        if flag not in argv and flag in usage:
            argv += [flag, value]
    # ``status`` takes its run id optionally, so the bracket-stripping above drops it, and
    # a ``status`` driven with no id lists your submissions and never reaches the dispatch
    # half of this verb. Supplied wherever the usage line names one, whichever way it
    # names it, because both halves are exit paths and both are what these cases are about.
    if "run_id" in usage and RUN_ID not in argv:
        argv.append(RUN_ID)
    # A lane verb's command is optional to argparse and required by the verb, which the
    # bracket-stripping above cannot see: ``run`` with no command prints a sentence and exits 2
    # without reaching a single call, so every case below would be asserting about a verb it
    # never drove. ``true`` because these cases are about how the binary exits and not about
    # what ran. The separator is the point of the flag and is what
    # ``test_the_command_s_own_flags_reach_it_rather_than_this_binary`` is about.
    if "command" in usage:
        argv += ["--", "true"]
    return argv


def driving(verb: str, *extra: str) -> list[str]:
    """One verb with enough to reach its work, plus flags meant for this binary.

    APPENDING WOULD PUT THEM ON THE WRONG SIDE OF A LANE VERB'S ``--``. Everything after that
    separator belongs to the researcher's own program, so ``run ... -- true --nonesuch x`` is
    not a mistake at all: it runs ``true --nonesuch x`` on a machine and exits 0, and a case
    about how a typo is reported would pass while reporting nothing. Spliced in ahead of the
    separator, which is where somebody typing a flag for ``edullm`` would put it.
    """
    argv = argv_for(verb)
    at = argv.index("--") if "--" in argv else len(argv)
    return [verb, *argv[:at], *extra, *argv[at:]]


SUBMIT_RUN = 19407766
CANCEL_RUN = 22001


def a_platform(
    tmp_path: Path, *, cancel_conclusion: str = "success", gh: Any = None
) -> FakeRunner:
    """A checkout that can submit, and a GitHub answering every call the verbs make.

    One fixture for all five verbs, because the cases below run over all five. The run the
    verbs are pointed at is admitted -- a dispatch of ``submit-run.yml`` carrying the id, and
    an admission job that succeeded -- which is what makes each of the three run verbs fall
    through to the dispatch this file's 3-versus-2 cases are about.

    **IT USED TO GET THERE BY ANSWERING "NOT FOUND" AND THAT WAS THE WRONG ROAD.** The
    compiled artifact was absent, so no dispatch could be joined to the id and ``status``,
    ``logs`` and ``cancel`` all fell through on the strength of not knowing. Two of those
    three now refuse instead, which is a change about run ids and not about exit codes, and
    it broke this file -- a file that is meant to be about exit codes. An admitted run is
    also what the overwhelming majority of these invocations really are, so the fixture now
    describes that rather than the one case with a verb-by-verb answer.
    """
    write_spec(tmp_path, workload="olmo-core-check", compute="gpu-1xt4")
    cancel_run = {
        "id": CANCEL_RUN,
        "status": "completed",
        "conclusion": cancel_conclusion,
        "created_at": "2099-01-01T00:00:00Z",
        "html_url": f"https://github.com/edu-llm/platform/actions/runs/{CANCEL_RUN}",
    }
    submit_run = {
        "id": SUBMIT_RUN,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2099-01-01T00:00:00Z",
        "html_url": f"https://github.com/edu-llm/platform/actions/runs/{SUBMIT_RUN}",
    }

    def api(argv: tuple[str, ...]) -> Any:
        path = argv[-1]
        if path.endswith(f"/{SUBMIT_RUN}/jobs"):
            return ok(json.dumps({"jobs": [{"name": ADMISSION_JOB, "conclusion": "success"}]}))
        if path.endswith(("/approvals", "/pending_deployments")):
            return ok(json.dumps([]))
        if SUBMIT_WORKFLOW in path:
            return ok(json.dumps({"workflow_runs": [submit_run]}))
        if path.endswith(str(CANCEL_RUN)):
            return ok(json.dumps(cancel_run))
        return ok(json.dumps({"workflow_runs": [cancel_run]}))

    def download(argv: tuple[str, ...]) -> Any:
        directory = Path(argv[argv.index("--dir") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "compiled-submission.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "experiment": "an-experiment",
                    "approval_class": "routine",
                    "approving_environment": "run-approval-lead",
                }
            ),
            encoding="utf-8",
        )
        return ok("")

    answers: dict[tuple[str, ...], Any] = dict(git_answers(tmp_path))
    answers[("gh", "workflow", "run")] = gh if gh is not None else ok("")
    answers[("gh", "api")] = gh if gh is not None else api
    answers[("gh", "run", "download")] = gh if gh is not None else download
    answers[("gh", "run", "view")] = gh if gh is not None else ok("")
    # ``ask`` is the one verb that neither dispatches a workflow nor reads one back, and it
    # is still a call to GitHub, so it belongs in the fixture that answers every call the
    # verbs make. Left out, it reaches FakeRunner's refusal to invent an answer and every
    # case below reports a fixture gap as a defect in the binary.
    answers[("gh", "issue", "create")] = (
        gh if gh is not None else ok("https://github.com/edu-llm/platform/issues/301\n")
    )
    # The lane verbs drive aws rather than gh, and they are in the population these cases run
    # over, so their calls belong here for the same reason ``gh issue create`` does. Merged
    # after the gh answers and not before, so a case that hands one ``gh`` answer to every call
    # still overrides the ones it means to.
    answers.update(lane_answers())
    # ``studio`` drives SageMaker rather than EC2, and it is in the population these cases run
    # over, so its calls belong here for the reason the lane's do. Its own answers rather than
    # a widened ``lane_answers``: the two are different surfaces, and a lane test carrying a
    # Studio domain it never reaches would be a fixture describing something untrue.
    answers.update(studio_answers())
    return FakeRunner(answers)


def code_of(argv: list[str], **kwargs: Any) -> int:
    """The exit code, whether the binary returned it or argparse raised it.

    ``--attempts nope`` leaves through ``SystemExit`` because argparse owns the refusal, and
    ``--hours nope`` leaves through a return because this binary parses that one by hand.
    Both are the same fact to a shell, so both are the same fact here.
    """
    try:
        code, _, _ = invoke(argv, **kwargs)
    except SystemExit as raised:
        return int(raised.code or 0)
    return code


# ---------------------------------------------------------------------------------------
# the four codes themselves
# ---------------------------------------------------------------------------------------


def test_the_binary_publishes_four_codes_and_the_signal_one_and_no_others() -> None:
    """Mutation: add a fifth class, or give two of them the same number.

    Four is ``gh``'s answer and is the number this picked deliberately. The AWS CLI uses
    eight, four of them in the 252 to 255 range a shell cannot easily tell from a signal;
    ripgrep and ruff use three and have no unreachable service to report. Read off the
    module rather than listed, so a constant added without a class to belong to fails here.
    """
    published = {
        name: value for name, value in vars(cli).items() if name.startswith("EXIT_")
    }

    assert set(published) == {name for name in cli.__all__ if name.startswith("EXIT_")}
    assert len(set(published.values())) == len(published)
    assert sorted(published.values()) == [0, 1, 2, 3, 128 + signal.SIGINT]


def test_every_flag_this_file_fills_in_is_a_flag_some_verb_still_takes() -> None:
    """Guards :data:`A_VALUE_FOR` against becoming a table of flags nobody takes.

    Mutation: rename ``--dataset``. Every case below would go on passing, because a value
    for a flag that does not exist is simply never supplied, and ``submit`` would quietly
    stop reaching the dispatch that half of these cases are about.
    """
    every_usage = " ".join(usage_of(verb) for verb in verbs())

    assert all(flag in every_usage for flag in A_VALUE_FOR)


def test_every_choice_this_file_fills_in_is_still_a_choice_that_verb_takes() -> None:
    """Guards :data:`A_CHOICE_FOR` the same way and against a worse silence.

    Mutation: rename ``repository`` to ``codebase``. Nothing here would go red. ``add`` would
    be driven with a kind argparse refuses, every case would collect its exit 2, and the two
    invariants that read ``if runner.ran("gh")`` would pass by having reached nothing at all.
    """
    for verb, choice in A_CHOICE_FOR.items():
        assert choice in choices_of(verb), f"{verb} no longer takes {choice!r}"


# ---------------------------------------------------------------------------------------
# 2, and what it stopped covering
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted({*BUILT_TODAY, *NOT_BUILT_YET}))
def test_a_flag_no_verb_takes_is_the_same_class_of_mistake_on_every_verb(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller's fault, on all nine, and never anything else.

    Mutation: answer one of them with a refusal. A refusal is a verdict about a submission
    and this is a verdict about a keystroke, so a caller that retried on 1 and gave up on 2
    would retry a typo until it ran out of patience.
    """
    runner = a_platform(tmp_path)

    code = code_of(
        driving(verb, "--nonesuch", "x"),
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_UNUSABLE


@pytest.mark.parametrize("flag", sorted({"--hours", "--attempts"}))
def test_a_number_that_is_not_a_number_is_one_class_whichever_flag_carried_it(
    flag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**One mistake under two codes, which is the shape a caller cannot write against.**

    Mutation: put ``--hours`` back on the refusal path. ``--attempts`` is ``type=int`` so
    argparse refuses a word for it and exits 2; ``--hours`` is parsed by hand, because a
    bound that went through binary floating point is not the number the approver reads, and
    it used to exit 1 as ``runtime_bound_not_a_number``. Two spellings of "that is not a
    number" answering a script two different ways is worse than either answer.

    Run over every verb whose usage line carries both flags rather than over ``check``
    alone, because they are added to ``submit`` by the same function and would have to be
    fixed on both.
    """
    carrying = sorted(verb for verb in verbs() if flag in usage_of(verb))
    runner = a_platform(tmp_path)

    codes = {
        verb: code_of(
            driving(verb, flag, "nope"),
            runner=runner,
            cwd=tmp_path,
            monkeypatch=monkeypatch,
        )
        for verb in carrying
    }

    assert carrying, f"no verb takes {flag}, so this case proved nothing"
    assert set(codes.values()) == {EXIT_UNUSABLE}, codes


# ---------------------------------------------------------------------------------------
# 3, which is the one that was not there
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted(BUILT_TODAY))
def test_a_github_that_will_not_answer_is_never_the_same_code_as_a_mistyped_flag(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The distinction a retry loop is made of, asserted as an invariant per verb.**

    Mutation: fold 3 back into 2. Sleeping and trying again is the first script anybody
    writes against this, and a caller that cannot tell an unresolvable host from a
    misspelled flag either retries the typo forever or retries nothing.

    Derived rather than listed: whichever verbs reach ``gh`` have to answer 3, and whichever
    do not are free to answer for themselves. ``check`` is in the second group and is meant
    to be, which ``tests/test_cli_check.py`` asserts directly.
    """
    runner = a_platform(tmp_path, gh=failed("could not resolve host: api.github.com"))

    code = code_of(
        [verb, *argv_for(verb)], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    if runner.ran("gh"):
        assert code == EXIT_UNREACHABLE
    else:
        assert code in {EXIT_OK, EXIT_REFUSED}


def test_some_verb_actually_reaches_github_in_that_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the invariant above against holding because nothing ever reached ``gh``."""
    reached = set()
    for verb in sorted(BUILT_TODAY):
        runner = a_platform(tmp_path, gh=failed("could not resolve host"))
        code_of([verb, *argv_for(verb)], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
        if runner.ran("gh"):
            reached.add(verb)

    assert SUBMIT_WORKFLOW  # the dispatch below is the one that matters most
    assert "submit" in reached and len(reached) > 1, reached


@pytest.mark.parametrize("verb", sorted(BUILT_TODAY))
def test_a_reporting_workflow_that_failed_never_reports_a_refused_submission(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The mapping that told a script a submission was declined by a verb that declines
    nothing.**

    Mutation: ``EXIT_OK if conclusion == "success" else EXIT_REFUSED``, which is what
    shipped. ``cancel-run.yml`` failing for its own reasons is a fact about a runner, and
    ``logs`` and ``status`` reported it as a verdict on the submission. A reader who acted
    on that goes and edits a spec that was fine, and a script that acted on it stops
    retrying the one thing here worth retrying.

    The verbs are not named. Whichever ones dispatch that workflow are the ones this holds
    over, so a fourth verb built onto the same report is covered on the day it is written.
    """
    runner = a_platform(tmp_path, cancel_conclusion="failure")

    code = code_of(
        [verb, *argv_for(verb)], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )
    drove_the_report = any(
        CANCEL_WORKFLOW in argv for argv in runner.ran("gh", "workflow", "run")
    )

    if drove_the_report:
        assert code == EXIT_UNREACHABLE


def test_some_verb_actually_drives_that_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the invariant above the same way, and against the same kind of nothing."""
    drove = set()
    for verb in sorted(BUILT_TODAY):
        runner = a_platform(tmp_path, cancel_conclusion="failure")
        code_of([verb, *argv_for(verb)], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
        if any(CANCEL_WORKFLOW in argv for argv in runner.ran("gh", "workflow", "run")):
            drove.add(verb)

    assert {"logs", "status", "cancel"} <= drove, drove


# ---------------------------------------------------------------------------------------
# 1, and 130
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("verb", sorted(BUILT_TODAY))
def test_a_run_id_nobody_could_read_is_refused_by_every_verb_that_takes_one(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: call it a usage error, which is arguably what it is.

    It is not, and the reason is what 1 means here. A run id that is not a run id is a
    verdict on a value the tool understood well enough to judge, and it carries a code and a
    remedy the way every other refusal does. Which verbs take one is read off the usage
    lines, so ``status`` taking an optional id and ``logs`` requiring one are both covered
    without either being named.
    """
    if "run_id" not in usage_of(verb):
        pytest.skip(f"{verb} takes no run id")
    runner = a_platform(tmp_path)
    argv = [word if word != RUN_ID else "not-a-run-id" for word in argv_for(verb)]

    code, _, err = invoke([verb, *argv], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "refused  run_id_not_well_formed" in err
    assert runner.ran("gh") == []


@pytest.mark.parametrize("verb", ["status", "logs"])
def test_a_run_id_nothing_recent_carries_is_a_second_refusal_with_a_second_code(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**A shape that is wrong and a run that is not here are two verdicts, not one.**

    Both are 1, because both are what 1 means: a verdict on the run id, which is the second
    thing ``MAINTAINING.md``'s table says a caller reading 1 should go and fix. What a script
    needs on top of the number is which of the two happened, because the remedies are
    different -- retype it, or pass ``--ask-aws`` -- and the codes are what carry that.

    Mutation: give this one 0 after a dispatch, which is what shipped. A verb that looks
    read-only spent a runner and could sit for eleven minutes over an id pasted out of an old
    transcript, then exited 0 as though it had answered.
    """
    # Hex letters and not zeros, for the reason ``_abbreviates_a_run_id`` fills its template
    # the same way: twelve digits in a row anywhere in this tree reads as an AWS account id,
    # and ``tests/test_evidence.py`` refuses one.
    unknown = "run_019fbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"
    runner = a_platform(tmp_path)
    argv = [word if word != RUN_ID else unknown for word in argv_for(verb)]

    code, out, err = invoke([verb, *argv], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert "refused  run_id_not_found" in err
    assert "run_id_not_well_formed" not in err
    assert runner.ran("gh", "workflow", "run") == []
    assert out == ""


@pytest.mark.parametrize("verb", sorted(BUILT_TODAY))
def test_an_interrupt_is_130_and_a_sentence_rather_than_a_traceback(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The one plainly broken thing in the tool, and it was broken by a type.**

    Mutation: catch ``Exception``, which is what was there. ``KeyboardInterrupt`` inherits
    from ``BaseException`` and not from ``Exception``, so it walked past a handler whose own
    comment says a researcher who meets a traceback learns the tool is broken, and Ctrl-C
    during any wait printed one ending inside ``time.sleep``.

    Raised from the runner rather than by signalling the process, because the signal arrives
    wherever the process happens to be and this has to hold for every verb. What it stands
    in for is the same interrupt landing in the sleep two frames further down.
    """

    def interrupted(*_: object, **__: object) -> None:
        raise KeyboardInterrupt

    runner = a_platform(tmp_path)
    monkeypatch.setattr(
        runner,
        "_answers",
        # ``aws`` beside the other two because the lane verbs drive it and nothing else, so a
        # dict naming only git and gh would meet FakeRunner's refusal to invent an answer on
        # those verbs and report a fixture gap as a missing interrupt handler.
        {("git",): interrupted, ("gh",): interrupted, ("aws",): interrupted},
    )

    code, _, err = invoke(
        [verb, *argv_for(verb)], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_INTERRUPTED
    assert "Traceback" not in err
    assert "interrupted" in err


def test_an_interrupt_after_a_dispatch_says_what_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The case the traceback was worst in, and the reason the message is not one line.**

    Mutation: print "interrupted" and stop. Every wait this binary imposes is after a
    dispatch, so the moment somebody reaches for Ctrl-C is the moment a workflow is already
    going. A bare "interrupted" reads as "nothing happened", which is false in the one case
    that has already spent a runner and may be about to spend a lead's approval.
    """
    runner = a_platform(tmp_path)
    dispatched: list[tuple[str, ...]] = []
    answered_before = ok(json.dumps({"workflow_runs": []}))

    def dispatch_then_wait(argv: tuple[str, ...]) -> Any:
        dispatched.append(argv)
        return ok("")

    def interrupt_the_wait(argv: tuple[str, ...]) -> Any:
        # The version probe reads an endpoint before the dispatch, so this stands in for a
        # Ctrl-C landing in the poll that follows the dispatch rather than in that one.
        if not dispatched:
            return answered_before
        raise KeyboardInterrupt

    runner._answers[("gh", "workflow", "run")] = dispatch_then_wait
    runner._answers[("gh", "api")] = interrupt_the_wait

    code, _, err = invoke(
        ["submit", *argv_for("submit")], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_INTERRUPTED
    assert SUBMIT_WORKFLOW in err
    assert "was already dispatched" in err
    assert "edullm status" in err


def test_an_interrupt_before_a_dispatch_says_nothing_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: name a workflow whatever happened.

    A message that reports a dispatch that never occurred sends somebody to Actions to look
    for a run that is not there, and teaches them not to believe the line next time, which
    is when it is true.
    """

    def interrupted(*_: object, **__: object) -> None:
        raise KeyboardInterrupt

    runner = a_platform(tmp_path)
    # Replaced rather than added to, because ``FakeRunner`` matches the longest prefix and
    # the git answers this fixture carries are all longer than ``("git",)``.
    monkeypatch.setattr(runner, "_answers", {("git",): interrupted})

    code, _, err = invoke(
        ["check", *argv_for("check")], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_INTERRUPTED
    assert "Nothing was dispatched" in err
    assert SUBMIT_WORKFLOW not in err and CANCEL_WORKFLOW not in err
