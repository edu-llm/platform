"""The verbs, and the argument parsing that reaches them.

EXIT CODES FOLLOW THE REPOSITORY'S CONVENTION, WHICH IS ALSO THE WORKFLOW'S: 0 for a
submission that stands, 1 for one that was refused on the merits, 2 for one nobody could
judge. ``tools/compile_submission.py`` separates the last two and says why in its own
header -- a refusal is a verdict a submitter can act on and an unreadable configuration is
not -- and a CLI that collapsed them would send somebody to edit a spec that was fine.

EVERY WORD THIS BINARY IS TYPED IS ONE OF THREE THINGS, AND THEY GET THREE ANSWERS.
``BUILT_TODAY`` is the governed-submission core -- ``check``, ``submit``, ``status``,
``logs``, ``cancel`` -- and runs. ``NOT_BUILT_YET`` is the rest of what
``docs-frank/reference/decisions.md`` settled on 2026-08-04, declared rather than absent so
that the answer to ``edullm shell`` is a plan rather than a usage error. ``RETIRED`` is the
names that were folded into those: ``dry-run`` and ``new`` into ``check``, ``activity``
into bare ``status``, ``notebook`` into ``shell --notebook``, and ``results`` into looking
at Weights and Biases. A typo is the fourth case and gets the nearest spelling and the list.

WHY THE RETIRED NAMES ARE REFUSED RATHER THAN ALIASED. Every transcript in
``docs-frank/working/terminal-mockups/`` types ``dry-run`` and ``new``, so accepting them
is tempting and wrong: an alias makes two names work and settles nothing, the retired one
survives into the next guide, and the rename never finishes. Fewer names is the direction
of this whole design -- ``check`` absorbed two verbs and ``status`` absorbed one -- and an
alias would quietly undo that. So the old spelling costs one retry, and what it buys is a
sentence naming what ``check`` would do in the repository the person is standing in, which
is the thing they were trying to find out.

NOTHING IN THIS PACKAGE WRITES A POLICY NUMBER DOWN. Every ceiling, rate and bound that
reaches a terminal is interpolated from the loaded configuration at the point of printing,
and ``tests/test_cli_no_hardcoded_bounds.py`` fails the build if one is written out. The
rule is structural because the alternative has already failed: the routine runtime bound
has disagreed between the documents and ``config/policy.yaml`` three separate times, every
one of them a second copy that was correct on the day it was typed.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from pathlib import Path
from typing import Final, TextIO

from edullm_platform.cli.actions import (
    CANCEL_WORKFLOW,
    PLATFORM_REPOSITORY,
    SUBMIT_WORKFLOW,
    GithubUnreachableError,
    PlatformActions,
    RunFacts,
    elapsed_said,
    read_report_sections,
    read_run_facts,
    read_submission_runs,
)
from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
    find_config_directory,
    load_reviewed_configuration,
)
from edullm_platform.cli.preflight import (
    Preflight,
    Refusal,
    SubmissionRequest,
    resolve_team,
    run_preflight,
    working_tree_refusals,
)
from edullm_platform.cli.presentation import (
    plain_decimal,
    render_preflight,
    render_refusals,
    render_run_facts,
    render_run_listing,
)
from edullm_platform.cli.scaffold import scaffold_spec
from edullm_platform.cli.spec import SPEC_PATH, RunSpec, SpecUnreadableError, find_spec, load_spec
from edullm_platform.cli.workspace import (
    CommandRunner,
    GitFacts,
    SubprocessRunner,
    ToolMissingError,
    github_login,
    read_git_facts,
)
from edullm_platform.contracts.identity import RUN_ID_REGEX

__all__ = ["EXIT_OK", "EXIT_REFUSED", "EXIT_UNUSABLE", "build_parser", "main"]

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_UNUSABLE: Final = 2

#: The five verbs that work, and the line each shows in ``--help`` and in the orientation a
#: bare ``edullm`` prints. One table rather than two so those two can never drift.
BUILT_TODAY: Final = {
    "check": "price a submission here; writes a first spec if there is none",
    "submit": "dispatch the submission workflow",
    "status": "what your runs are doing",
    "logs": "the last lines a run printed",
    "cancel": "stop a run",
}

#: The verbs that are settled and unbuilt, with the sentence each prints. Present so the
#: binary can say "not built yet" rather than "invalid choice", which are different facts:
#: one is a plan and the other is a typo.
NOT_BUILT_YET: Final = {
    "run": "ship this working tree to a machine and stream the output back",
    "shell": "open an editor over SSH on a machine, with --notebook for Jupyter",
    "add": "teach the platform about a repository, dataset, shape, model or person",
    "ask": "ask for something for yourself, which produces a time-boxed grant",
}

#: The names that were something and are now something else, and what to type instead.
#:
#: NOT ALIASES, AND THE DIFFERENCE IS THE WHOLE REASON THIS TABLE EXISTS. An alias makes
#: two names work and teaches nobody which one is the name; the second one then appears in
#: a guide somebody writes next month and the rename never finishes. Reducing the number of
#: names is a running theme of this design -- ``check`` absorbed two verbs, ``status``
#: absorbed one, ``notebook`` folded into a flag -- and an alias would undo that quietly.
#:
#: So the retired name is refused, and the refusal names the replacement. Four of the five
#: are ``decisions.md``'s own foldings; ``results`` is the one that comes from the mockups
#: instead, where ``nathan-zhao-curriculum-matrix.md`` records it being cut and the
#: comparison moving into Weights and Biases.
RETIRED: Final = {
    "dry-run": (
        "check",
        "dry-run is not a verb. check is.",
        (
            "check validates a submission completely on this machine and prints exactly "
            "what would be sent. It reaches no network and queues nothing, which is the "
            "whole of what dry-run meant."
        ),
    ),
    "new": (
        "check",
        "new is not a verb. check is, and it scaffolds.",
        (
            "check writes the spec when a repository has none and then prices it, so the "
            "first command a newcomer types is also the one that gets them a file."
        ),
    ),
    "activity": (
        "status",
        "activity is not a verb. status is.",
        (
            "`edullm status` with no run id is what activity was going to be: your recent "
            "submissions and what each is doing. Naming one run narrows it to that run."
        ),
    ),
    "notebook": (
        "shell",
        "notebook is not a verb. It folded into a flag: `edullm shell --notebook`.",
        (
            "One machine, two clients -- an editor over SSH or Jupyter, chosen at the "
            "point of asking rather than by picking a different verb. shell is not built "
            "yet, so neither spelling runs today."
        ),
    ),
    "results": (
        None,
        "results is not a verb, and nothing replaced it here.",
        (
            "Comparing runs happens in Weights and Biases, where the numbers already are "
            "and where a chart is a chart rather than a table redrawn in a terminal. "
            "`edullm status <run-id>` prints that run's link."
        ),
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edullm",
        description=(
            "Submit and follow runs on the eduLLM platform without opening the Actions UI."
        ),
    )
    parser.add_argument("--version", action="version", version=f"edullm {_installed_version()}")
    # ON EVERY SUBCOMMAND RATHER THAN ON THE ROOT, WHICH IS WHAT LETS THE FIRST WORD BE
    # READ AS THE VERB WITHOUT PARSING ANYTHING. A root option taking a value puts a
    # non-flag word in front of the verb -- `edullm --config-dir /tmp check` -- so the
    # teaching path below could not tell that word from a mistyped verb, and a typo would
    # be answered with argparse's own "invalid choice" listing the retired names as though
    # they worked.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config-dir",
        type=Path,
        help=(
            "the reviewed configuration to check against. Defaults to the copy this "
            "install carries, so that what edullm refuses is what the platform at this "
            "version refuses."
        ),
    )
    common.add_argument(
        "--platform-repository",
        default=PLATFORM_REPOSITORY,
        help="where the submission workflows live",
    )
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="verb")

    check = verbs.add_parser("check", parents=[common], help=BUILT_TODAY["check"])
    _add_submission_arguments(check)

    submit = verbs.add_parser("submit", parents=[common], help=BUILT_TODAY["submit"])
    _add_submission_arguments(submit)
    submit.add_argument(
        "--no-wait",
        action="store_true",
        help="dispatch and return, rather than waiting for the run id",
    )
    submit.add_argument(
        "--force",
        action="store_true",
        help=(
            "dispatch even where the local checks refuse. Every refusal it skips is one "
            "admission makes again from inside AWS, so this buys a queue wait rather than "
            "an outcome."
        ),
    )

    status = verbs.add_parser("status", parents=[common], help=BUILT_TODAY["status"])
    status.add_argument("run_id", nargs="?", help="one run; omit for your recent submissions")

    logs = verbs.add_parser("logs", parents=[common], help=BUILT_TODAY["logs"])
    logs.add_argument("run_id", help="the run to read")

    cancel = verbs.add_parser("cancel", parents=[common], help=BUILT_TODAY["cancel"])
    cancel.add_argument("run_id", help="the run to stop")
    cancel.add_argument(
        "--reason",
        required=True,
        help=(
            "why. Recorded on the termination, which is what lets the run's history say it "
            "was cancelled rather than that it broke."
        ),
    )

    for verb, description in NOT_BUILT_YET.items():
        verbs.add_parser(verb, parents=[common], help=f"not built yet: {description}")
    return parser


def _no_such_verb(word: str, *, cwd: Path) -> str:
    """A word the binary does not know, answered with the word it does.

    THE STATE SENTENCE IS THE POINT WHEN THE REPLACEMENT IS ``check``. "Type check instead"
    is a redirection; "here, check would write a first spec and then price it" is the
    answer to what the person was trying to find out, and it costs one directory walk.
    """
    lines = [""]
    entry = RETIRED.get(word)
    if entry is not None:
        replacement, headline, explanation = entry
        lines += [headline, "", *_wrapped(explanation)]
        if replacement == "check":
            lines += ["", *_wrapped(f"From this directory, check would {_what_check_would_do(cwd)}.")]
        if replacement is not None and replacement in NOT_BUILT_YET:
            lines += ["", f"  edullm {replacement}   is not built yet"]
        elif replacement is not None:
            lines += ["", f"  edullm {replacement}"]
        return "\n".join([*lines, ""])

    known = sorted({*BUILT_TODAY, *NOT_BUILT_YET, *RETIRED})
    near = get_close_matches(word, known, n=1, cutoff=0.6)
    lines += [f"{word} is not a verb."]
    if near:
        lines += ["", f"Did you mean {near[0]}?"]
    lines += ["", "These are:"]
    lines += [f"  {verb:<8} {summary}" for verb, summary in BUILT_TODAY.items()]
    lines += [f"  {verb:<8} not built yet" for verb in NOT_BUILT_YET]
    return "\n".join([*lines, ""])


def _orientation(*, cwd: Path) -> str:
    """What a bare ``edullm`` says, which for most people is the first thing it ever says."""
    lines = [
        "",
        "edullm submits and follows runs on the eduLLM platform, so that nobody has to",
        "open the Actions UI. These verbs work:",
        "",
    ]
    lines += [f"  {verb:<8} {summary}" for verb, summary in BUILT_TODAY.items()]
    lines += [
        "",
        *_wrapped(f"Start with check. Here it would {_what_check_would_do(cwd)}."),
        "",
        "  edullm check --help    the flags one submission takes",
        "",
    ]
    return "\n".join(lines)


def _what_check_would_do(cwd: Path) -> str:
    """Read from the filesystem rather than asserted, so the sentence is about here."""
    found = find_spec(cwd)
    if found is not None:
        return f"price {found} and list every refusal, dispatching nothing"
    return (
        f"write a first {SPEC_PATH} -- there is none at or above here -- and then price it"
    )


def _wrapped(text: str) -> list[str]:
    """Wrapped at spaces and at nothing else, because these paragraphs carry paths.

    ``textwrap`` breaks on hyphens by default, and the paths this prints are full of them --
    a wrapped ``/tmp/pytest-of-frank/...`` comes back as ``pytest-`` and ``of-frank`` on two
    lines, which is a path nobody can copy and one that does not exist.
    """
    return textwrap.wrap(
        text,
        width=78,
        initial_indent="  ",
        subsequent_indent="  ",
        break_on_hyphens=False,
        break_long_words=False,
    )


def _installed_version() -> str:
    """What ``pip`` thinks is installed, or a working tree's honest admission that nothing is."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("edullm-platform")
    except PackageNotFoundError:
        return "(not installed)"


def _add_submission_arguments(parser: argparse.ArgumentParser) -> None:
    """The fields a submission needs that are not properties of the code.

    The split is ``system-overview.md``'s: what a run does travels in ``.edullm/run.yaml``
    with the code, and what somebody is paying for today is typed here. ``--workload`` and
    ``--compute`` appear on both sides because the spec's values are a default and an
    override, and every other flag here has nowhere else it could come from.
    """
    parser.add_argument("--experiment", help="how this run groups with its neighbours")
    parser.add_argument("--dataset", help="the corpus this run reads, or none")
    parser.add_argument("--compute", help="the machine, overriding the spec's suggestion")
    parser.add_argument("--workload", help="the workload profile, overriding the spec's")
    parser.add_argument("--team", help="the group this run is charged to")
    parser.add_argument(
        "--wandb-project", help="the Weights and Biases project; defaults to the team"
    )
    parser.add_argument("--repository", help="overriding what the origin remote says")
    parser.add_argument("--commit", help="overriding HEAD")
    parser.add_argument("--hours", help="override the workload's runtime bound")
    parser.add_argument("--attempts", type=int, help="override the workload's attempt bound")
    parser.add_argument("--fanout-size", type=int, help="cells in a fan-out")
    parser.add_argument("--fanout-index-parameter", help="what the fan-out index varies")
    parser.add_argument("--spec", type=Path, help=f"a spec other than the {SPEC_PATH} above you")


def main(
    argv: list[str] | None = None,
    *,
    runner: CommandRunner | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    cwd: Path | None = None,
) -> int:
    """Parse, dispatch, and turn anything that escapes into the right kind of exit.

    The four keyword arguments are what make the suite hermetic. Nothing else in this
    package reaches a process, a stream or the filesystem's idea of where it is standing.
    """
    tokens = sys.argv[1:] if argv is None else list(argv)
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err
    command_runner: CommandRunner = SubprocessRunner() if runner is None else runner
    here = Path.cwd() if cwd is None else cwd

    # BEFORE ARGPARSE, BECAUSE ARGPARSE'S ANSWER TO A WORD IT DOES NOT KNOW IS A LIST OF
    # WORDS IT DOES. That is the right answer for a typo and the wrong one for a rename: a
    # researcher who types `dry-run` because a guide said so needs to be told the verb was
    # renamed and what it is now, not handed a menu to search.
    word = tokens[0] if tokens and not tokens[0].startswith("-") else None
    if not tokens:
        print(_orientation(cwd=here), end="", file=stderr)
        return EXIT_UNUSABLE
    if word is not None and word not in BUILT_TODAY and word not in NOT_BUILT_YET:
        print(_no_such_verb(word, cwd=here), end="", file=stderr)
        return EXIT_UNUSABLE

    arguments = build_parser().parse_args(tokens)
    verb = arguments.verb
    if verb in NOT_BUILT_YET:
        print(
            f"{verb} is not built yet. It is settled -- it would "
            f"{NOT_BUILT_YET[verb]} -- and nothing behind it exists. Built today: "
            f"{', '.join(BUILT_TODAY)}.",
            file=stderr,
        )
        return EXIT_UNUSABLE

    try:
        if verb == "check":
            return _check(arguments, runner=command_runner, out=stdout, err=stderr, cwd=here)
        if verb == "submit":
            return _submit(arguments, runner=command_runner, out=stdout, err=stderr, cwd=here)
        if verb == "status":
            return _status(arguments, runner=command_runner, out=stdout, err=stderr)
        if verb == "logs":
            return _logs(arguments, runner=command_runner, out=stdout, err=stderr)
        if verb == "cancel":
            return _cancel(arguments, runner=command_runner, out=stdout, err=stderr)
    except (ConfigurationUnreadableError, SpecUnreadableError, ToolMissingError) as exc:
        print(str(exc), file=stderr)
        return EXIT_UNUSABLE
    except GithubUnreachableError as exc:
        # Never EXIT_REFUSED. GitHub being unreachable says nothing about the submission,
        # and the workflow makes the same separation in its own exit codes.
        print(str(exc), file=stderr)
        return EXIT_UNUSABLE
    raise AssertionError(f"unreachable verb {verb!r}")


# ---------------------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------------------


def _check(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    cwd: Path,
) -> int:
    configuration = _configuration(arguments)
    facts = read_git_facts(runner, cwd=cwd)
    submitter = github_login(runner, allow_network=False)
    spec, scaffolded = _spec_for_checking(arguments, configuration, facts, cwd=cwd)
    if scaffolded is not None:
        print(f"wrote {scaffolded}", file=out)
        print(file=out)
    preflight = _preflight(arguments, configuration, facts, spec, submitter)
    print(render_preflight(preflight, policy=configuration.policy), end="", file=out)
    return EXIT_REFUSED if preflight.refused else EXIT_OK


def _spec_for_checking(
    arguments: argparse.Namespace,
    configuration: ReviewedConfiguration,
    facts: GitFacts,
    *,
    cwd: Path,
) -> tuple[RunSpec | None, Path | None]:
    """``check`` absorbing ``new``: a repository with no spec gets one, then gets checked.

    Written rather than offered, because the alternative is a prompt and a prompt is what
    stops an agent driving this. What makes writing safe is that everything in the file is
    either read from the catalog or is the reviewed default the form itself carries, and
    the check that follows immediately says which of them will not do.
    """
    declared = arguments.spec if getattr(arguments, "spec", None) else None
    if declared is not None:
        return load_spec(declared), None
    found = find_spec(cwd)
    if found is not None:
        return load_spec(found), None
    if facts.root is None or facts.repository is None:
        return None, None
    written = scaffold_spec(
        configuration,
        repository=facts.repository,
        root=facts.root,
        workload_profile=arguments.workload,
        compute_profile=arguments.compute,
    )
    return load_spec(written), written


# ---------------------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------------------


def _submit(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    cwd: Path,
) -> int:
    configuration = _configuration(arguments)
    facts = read_git_facts(runner, cwd=cwd)
    submitter = github_login(runner, allow_network=True)
    declared = arguments.spec if arguments.spec else find_spec(cwd)
    spec = load_spec(declared) if declared is not None else None
    preflight = _preflight(arguments, configuration, facts, spec, submitter)

    if preflight.refused and not arguments.force:
        print(render_refusals(preflight.refusals), end="", file=err)
        print(
            "Nothing was dispatched. Every one of these is a refusal admission makes too, "
            "so submitting anyway costs a queue wait and reaches the same answer.",
            file=err,
        )
        return EXIT_REFUSED
    if preflight.refused:
        print(
            f"--force: dispatching over {len(preflight.refusals)} local refusal(s).",
            file=err,
        )

    actions = PlatformActions(runner, repository=arguments.platform_repository)
    dispatched_at = datetime.now(UTC)
    actions.dispatch(SUBMIT_WORKFLOW, _submission_form(preflight.request))
    print(f"dispatching {SUBMIT_WORKFLOW} ... queued", file=out)
    if arguments.no_wait:
        print(
            "not waiting. edullm status names the run once the compile job has run.",
            file=out,
        )
        return EXIT_OK

    run = actions.wait_for_a_new_run(SUBMIT_WORKFLOW, actor=submitter, after=dispatched_at)
    if run is None:
        print(
            "dispatched, and the workflow run it started could not be found within the "
            "poll window. It is on its way; edullm status will name it.",
            file=out,
        )
        return EXIT_OK
    identifier = int(run["id"])
    print(str(run.get("html_url") or ""), file=out)
    compiled = actions.compiled_submission(identifier)
    if compiled is None:
        print(
            "compiling. The run id is issued by the compile job; edullm status will carry "
            "it once that job has finished.",
            file=out,
        )
        return EXIT_OK
    print(file=out)
    print(str(compiled.get("run_id") or "unknown"), file=out)
    approval_class = str(compiled.get("approval_class") or "")
    environment = str(compiled.get("approving_environment") or "")
    if approval_class == "automatic":
        print("released automatically. Nothing is waiting on a person.", file=out)
    else:
        print(
            f"waiting at {environment}. Any of the nine approvers can release it.",
            file=out,
        )
    return EXIT_OK


def _submission_form(request: SubmissionRequest) -> dict[str, str]:
    """``SubmissionInputs`` field for field, which is what the form is.

    ``image_digest`` is deliberately absent rather than empty. The workflow derives it from
    the declared commit and the field survives only as an override for a deliberate
    rebuild-and-pin; sending a value this binary made up would be that override, aimed at
    nothing.
    """
    fields = {
        "repository": request.repository,
        "commit_sha": request.commit_sha,
        "workload_profile": request.workload_profile,
        "compute_profile": request.compute_profile,
        "dataset_release": request.dataset_release,
        "team": request.team,
        "experiment": request.experiment,
        "wandb_project": request.wandb_project,
        # Rejoined the way the workflow will split it. ``shlex.join`` would quote every
        # word, and a quoted ``"$EDULLM_CHECKPOINT_DIR"`` reaches OLMo-core as twenty-two
        # literal characters and a directory it cheerfully creates.
        "command": " ".join(request.command),
    }
    if request.maximum_runtime_hours is not None:
        fields["maximum_runtime_hours"] = format(request.maximum_runtime_hours, "f")
    if request.maximum_attempts is not None:
        fields["maximum_attempts"] = str(request.maximum_attempts)
    if request.fanout_size is not None and request.fanout_index_parameter is not None:
        fields["fanout_size"] = str(request.fanout_size)
        fields["fanout_index_parameter"] = request.fanout_index_parameter
    return fields


# ---------------------------------------------------------------------------------------
# status, logs, cancel
# ---------------------------------------------------------------------------------------


def _status(
    arguments: argparse.Namespace, *, runner: CommandRunner, out: TextIO, err: TextIO
) -> int:
    actions = PlatformActions(runner, repository=arguments.platform_repository)
    if arguments.run_id is None:
        submitter = github_login(runner, allow_network=True)
        runs = read_submission_runs(actions, actor=submitter)
        print(
            render_run_listing(
                (
                    run.short_run_id,
                    run.state,
                    elapsed_said(run.created_at),
                    " ".join(
                        part
                        for part in (
                            run.experiment or "",
                            f"{run.cells} cells" if run.cells else "",
                        )
                        if part
                    ),
                )
                for run in runs
            ),
            end="",
            file=out,
        )
        return EXIT_OK

    refusal = _malformed_run_id(arguments.run_id)
    if refusal is not None:
        print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED

    facts = read_run_facts(actions, arguments.run_id)
    print(render_run_facts(facts), end="", file=out)
    if not facts.needs_a_dispatch:
        return EXIT_OK
    print(file=out)
    return _drive_the_run_report(
        actions,
        run_id=arguments.run_id,
        stop=False,
        reason=None,
        headings=(arguments.run_id, "Runs submitted by", "No runs found"),
        out=out,
        err=err,
    )


def _logs(
    arguments: argparse.Namespace, *, runner: CommandRunner, out: TextIO, err: TextIO
) -> int:
    refusal = _malformed_run_id(arguments.run_id)
    if refusal is not None:
        print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED
    actions = PlatformActions(runner, repository=arguments.platform_repository)
    facts = read_run_facts(actions, arguments.run_id)
    if not facts.needs_a_dispatch:
        # A run that never reached AWS printed nothing there. Dispatching would spend a
        # runner to be told that, and what came back would read as an empty log rather than
        # as a run that has not started, which are different facts.
        print(f"{facts.run_id} has printed nothing yet.", file=out)
        print(file=out)
        print(_because(facts), end="", file=out)
        return EXIT_OK
    return _drive_the_run_report(
        actions,
        run_id=arguments.run_id,
        stop=False,
        reason=None,
        headings=("The last lines this run printed",),
        because=facts.because,
        out=out,
        err=err,
    )


def _cancel(
    arguments: argparse.Namespace, *, runner: CommandRunner, out: TextIO, err: TextIO
) -> int:
    refusal = _malformed_run_id(arguments.run_id)
    if refusal is not None:
        print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED
    actions = PlatformActions(runner, repository=arguments.platform_repository)
    facts = read_run_facts(actions, arguments.run_id)
    if not facts.needs_a_dispatch:
        # THE ONE PLACE THIS SHORTCUT COULD DO HARM, WHICH IS WHY ``Admitted`` HAS THREE
        # VALUES. Refusing to stop a job that is in fact running would be far worse than a
        # wasted runner, so ``NO`` is returned only where the admission job demonstrably did
        # not run. An admission job that failed at an unknown point reads UNSURE and
        # dispatches, which is what this verb did before the fast path existed.
        #
        # REFUSED RATHER THAN OK, THOUGH NOTHING IS RUNNING. A run parked at a gate is not
        # stopped by having no Batch job: a lead can still release it, and it will start.
        # Exiting 0 would tell a script the run was seen to and leave it live.
        print(
            render_refusals(
                [
                    Refusal(
                        code="nothing_admitted_to_stop",
                        detail=(
                            f"{facts.run_id} has no Batch job, because {facts.because} "
                            "Stopping the submission itself is a GitHub operation rather "
                            "than an AWS one: "
                            + (
                                f"gh run cancel {facts.submission.workflow_run_id} --repo "
                                f"{actions.repository}"
                                if facts.submission is not None
                                else "cancel the workflow run on its own page"
                            )
                            + ". Left alone, an approval would still start it."
                        ),
                    )
                ]
            ),
            end="",
            file=out,
        )
        return EXIT_REFUSED
    return _drive_the_run_report(
        actions,
        run_id=arguments.run_id,
        stop=True,
        reason=arguments.reason,
        headings=("Run stopped", arguments.run_id),
        because=facts.because,
        out=out,
        err=err,
    )


def _because(facts: RunFacts) -> str:
    """The one-line explanation, wrapped the way every other paragraph here is."""
    return "\n".join(textwrap.wrap(facts.because, width=78)) + "\n"


def _drive_the_run_report(
    actions: PlatformActions,
    *,
    run_id: str,
    stop: bool,
    reason: str | None,
    headings: Sequence[str],
    because: str | None = None,
    out: TextIO,
    err: TextIO,
) -> int:
    """Dispatch ``cancel-run.yml`` and print the part of its report that was asked for.

    One workflow behind three verbs, because that is how many the workflow is: its own name
    is "Look at a run, or stop it", looking is the default and stopping is the opt-in, and
    the log tail is printed on every dispatch including one that goes on to stop the run --
    "somebody about to cancel twelve hours of work should see what they are cancelling in
    the same output".

    It is slow and saying so is part of the contract. A runner has to start, so this is tens
    of seconds where a transcript reads as instant. The alternative is an AWS credential on
    every laptop, which is the arrangement the whole design is an argument against.
    """
    fields = {"run_id": run_id, "stop": "true" if stop else "false"}
    if reason is not None:
        fields["reason"] = reason
    dispatched_at = datetime.now(UTC)
    # SAID BEFORE THE WAIT RATHER THAN AFTER IT. Tens of seconds a reader understands is a
    # different experience from tens of seconds they do not, and this is the only place the
    # binary makes anybody wait. The sentence names the cause -- a credential that lives in
    # one workflow file and nowhere else -- because the alternative reads as slowness.
    #
    # ``because`` is what the fast path could not settle, for the verbs that do not print
    # the whole of what it found. Without it a reader who has watched status answer three
    # runs instantly has no way to tell why this one is different.
    if because:
        print(because, file=err)
    print(
        f"asking AWS about {run_id}. This dispatches {CANCEL_WORKFLOW}, which holds the "
        "only identity that may read a Batch job, so it waits for a runner: tens of "
        "seconds, not a moment.",
        file=err,
    )
    actions.dispatch(CANCEL_WORKFLOW, fields)
    run = actions.wait_for_a_new_run(CANCEL_WORKFLOW, actor=None, after=dispatched_at)
    if run is None:
        print(
            "dispatched, and the workflow run it started could not be found within the "
            f"poll window. It is running; the {CANCEL_WORKFLOW} page carries its answer.",
            file=err,
        )
        return EXIT_UNUSABLE
    identifier = int(run["id"])
    conclusion = actions.wait_for_completion(identifier)
    report = read_report_sections(actions.job_log(identifier), headings)
    if report:
        print(report, file=out)
    else:
        print(
            f"the workflow finished {conclusion} and its report named no section this verb "
            f"reads. The whole of it is at {run.get('html_url')}.",
            file=err,
        )
    return EXIT_OK if conclusion == "success" else EXIT_REFUSED


def _malformed_run_id(run_id: str) -> Refusal | None:
    """The same shape ``cancel-run.yml`` checks before it touches AWS, checked before that.

    That workflow refuses a malformed id in its first step, deliberately, because a mistake
    is a thing to answer in the workflow rather than a call to make with a credential in
    hand. The same argument one layer out is stronger: here it costs no runner either.
    """
    if RUN_ID_REGEX.fullmatch(run_id):
        return None
    return Refusal(
        code="run_id_not_well_formed",
        detail=(
            f"{run_id!r} is not a run id. One reads run_ followed by a UUID and is printed "
            "by the submission that started it. edullm status with no argument lists yours."
        ),
    )


# ---------------------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------------------


def _configuration(arguments: argparse.Namespace) -> ReviewedConfiguration:
    return load_reviewed_configuration(find_config_directory(override=arguments.config_dir))


def _preflight(
    arguments: argparse.Namespace,
    configuration: ReviewedConfiguration,
    facts: GitFacts,
    spec: RunSpec | None,
    submitter: str | None,
) -> Preflight:
    """Merge the spec, the flags and the working tree, then run every local check.

    Where a value can come from two places the flag wins, and the spec is the default. That
    is the direction ``system-overview.md`` sets for the machine -- a suggestion in version
    control, a decision at submit time -- and applying the same rule to the workload keeps
    a submitter from having to edit a committed file to try a different profile once.
    """
    refusals: list[Refusal] = working_tree_refusals(facts)
    if spec is None:
        refusals.append(
            Refusal(
                code="no_run_spec",
                detail=(
                    f"there is no {SPEC_PATH} at or above here, and none could be written "
                    "because this is not a checkout of a registered repository. check "
                    "writes a first one from inside a checkout; it holds what is a property "
                    "of the code -- the command, the workload profile and a suggested "
                    "machine."
                ),
            )
        )
    team, team_source, team_refusal = (
        (arguments.team, "named on the command line", None)
        if arguments.team
        else resolve_team(configuration, submitter=submitter)
    )
    if team_refusal is not None:
        refusals.append(team_refusal)
    missing = _missing_required(arguments, spec, team, configuration)
    refusals.extend(missing)

    if spec is None or team is None or missing or facts.commit_sha is None:
        return Preflight(
            request=_partial_request(arguments, spec, facts, team),
            refusals=tuple(refusals),
            team_source=team_source,
        )

    hours, hours_refusal = _decimal_hours(arguments.hours)
    if hours_refusal is not None:
        refusals.append(hours_refusal)
    request = SubmissionRequest(
        repository=arguments.repository or facts.repository or "",
        commit_sha=arguments.commit or facts.commit_sha,
        workload_profile=arguments.workload or spec.workload_profile,
        compute_profile=arguments.compute or spec.suggested_compute or "",
        dataset_release=arguments.dataset,
        team=team,
        experiment=arguments.experiment,
        # THE PROJECT IS THE TEAM UNLESS SOMEBODY SAYS OTHERWISE, WHICH IS WHAT W&B ALREADY
        # IS HERE. ``decisions.md`` records the consequence as a limitation -- "a W&B
        # project is a team", which is why a cross-team comparison cannot exist -- and every
        # run URL in every transcript is wandb.ai/eduLLM/<team>/. Defaulting to anything
        # else would put two runs meant to be compared on two pages, which the overview
        # already lists under Not built yet as the grouping problem.
        wandb_project=arguments.wandb_project or team,
        command=spec.argv,
        maximum_runtime_hours=hours,
        maximum_attempts=arguments.attempts,
        fanout_size=arguments.fanout_size
        or (spec.fanout.size if spec.fanout is not None else None),
        fanout_index_parameter=arguments.fanout_index_parameter
        or (spec.fanout.index_parameter if spec.fanout is not None else None),
    )
    preflight = run_preflight(
        request,
        configuration=configuration,
        submitter=submitter,
        team_source=team_source,
    )
    return Preflight(
        request=preflight.request,
        refusals=(*refusals, *preflight.refusals),
        team_source=preflight.team_source,
        workload=preflight.workload,
        compute=preflight.compute,
        dataset=preflight.dataset,
        manifest=preflight.manifest,
        cost=preflight.cost,
        approval_class=preflight.approval_class,
        approving_environment=preflight.approving_environment,
        exceeded=preflight.exceeded,
    )


def _missing_required(
    arguments: argparse.Namespace,
    spec: RunSpec | None,
    team: str | None,
    configuration: ReviewedConfiguration,
) -> list[Refusal]:
    """The fields with no default anywhere, named one at a time rather than as a usage line.

    argparse would make these required and print a usage string, and a usage string is the
    wrong answer for two of them: the machine and the workload have a default in the spec,
    so whether they are required depends on a file argparse has not read. Saying which one
    is missing and where it could have come from is the same courtesy the refusals get.
    """
    refusals: list[Refusal] = []
    if not arguments.experiment:
        refusals.append(
            Refusal(
                code="no_experiment",
                detail=(
                    "--experiment names how this run groups with its neighbours, and there "
                    "is no default for it: it is the one field that says which question a "
                    "run is part of answering. It registers nothing, so any lower-case "
                    "hyphenated name will do."
                ),
            )
        )
    if not arguments.dataset:
        refusals.append(
            Refusal(
                code="no_dataset",
                detail=(
                    "--dataset names the corpus this run reads. Pass none where it reads "
                    "nothing, which is what a check, a tokenization or an evaluation over "
                    "checkpoints does -- absent and none are different answers and only one "
                    "of them is a statement."
                ),
            )
        )
    if spec is not None and not (arguments.compute or spec.suggested_compute):
        refusals.append(
            Refusal(
                code="no_compute_profile",
                detail=(
                    f"neither --compute nor a suggested_compute in {SPEC_PATH} names a "
                    "machine. It is the most expensive field on a submission by some "
                    f"distance -- {_rate_span(configuration)} -- and there is nothing to "
                    "derive it from, so it is asked rather than defaulted."
                ),
            )
        )
    return refusals


def _rate_span(configuration: ReviewedConfiguration) -> str:
    """The catalog's cheapest and dearest hourly rates, read at the moment of printing.

    Written out, this sentence said ``$0.526 an hour against $55.04``, which was true of the
    catalog on the day it was typed and is a second copy of two numbers that live in
    ``compute-catalog.yaml``. Adding a shape would have left the refusal quietly stale, and
    a refusal that argues from a wrong price is worse than one that argues from none.
    """
    rates = [
        profile.hourly_rate_usd
        for profile in configuration.catalog.compute_profiles
        if profile.provisioned
    ]
    if not rates:
        return "the catalog's rates span orders of magnitude"
    return f"${plain_decimal(min(rates))} an hour against ${plain_decimal(max(rates))}"


def _partial_request(
    arguments: argparse.Namespace,
    spec: RunSpec | None,
    facts: GitFacts,
    team: str | None,
) -> SubmissionRequest:
    """Whatever is known, so a refusal can still say what it was refusing."""
    return SubmissionRequest(
        repository=arguments.repository or facts.repository or "",
        commit_sha=arguments.commit or facts.commit_sha or "",
        workload_profile=arguments.workload or (spec.workload_profile if spec else ""),
        compute_profile=arguments.compute or (spec.suggested_compute if spec else "") or "",
        dataset_release=arguments.dataset or "",
        team=team or "",
        experiment=arguments.experiment or "",
        wandb_project=arguments.wandb_project or team or "",
        command=spec.argv if spec else (),
    )


def _decimal_hours(text: str | None) -> tuple[Decimal | None, Refusal | None]:
    """``--hours`` as base-ten text, which is the shape the whole path carries it in.

    The workflow's own comment says why: a bound that went through binary floating point is
    not the number the approver reads. So it is parsed as a decimal here and formatted back
    to text before it reaches the form, and never becomes a float on the way.
    """
    if text is None:
        return None, None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        parsed = Decimal(0)
    if not parsed.is_finite() or parsed <= 0:
        return None, Refusal(
            code="runtime_bound_not_a_number",
            detail=(
                f"--hours takes a positive base-ten number of hours and was given {text!r}. "
                "Fractions are fine -- 0.5 is thirty minutes -- and the bound is what the "
                "worst case multiplies, so lowering it is what moves a short run under the "
                "automatic bound."
            ),
        )
    return parsed, None
