"""The verbs, and the argument parsing that reaches them.

FOUR EXIT CODES AND THE SIGNAL ONE, AND EVERY PATH OUT OF THIS BINARY IS ONE OF THEM. 0 for
a submission that stands, 1 for one refused on the merits, 2 for a tool nobody could drive,
3 for a platform nobody could ask, and 130 for an interrupt. ``MAINTAINING.md`` carries the
table and the argument; what matters here is that the five are exhaustive, so a verb that
grows a new way to fail has to say which of them it is.

WHY 2 AND 3 ARE NOT ONE CODE, WHICH IS THE ONLY ONE OF THE FIVE THAT IS NOT OBVIOUS. They
were, and it made the first script anybody writes impossible. A mistyped flag and a GitHub
that would not answer both exited 2, so a caller that wanted to sleep and try again had to
either retry a typo forever or never retry anything. The caller's fault is worth reporting
and the platform's is worth retrying, and no amount of reading stderr recovers a
distinction the exit code threw away. ``tools/compile_submission.py`` makes the same
separation between 1 and 2 for the same reason: a refusal is a verdict a submitter can act
on and an unreadable configuration is not, and a CLI that collapsed them would send
somebody to edit a spec that was fine.

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

NOTHING IN THIS PACKAGE WRITES A POLICY NUMBER DOWN. Every ceiling, rate, bound and count
that reaches a terminal is interpolated from the loaded configuration at the point of
printing, and ``tests/test_cli_no_hardcoded_bounds.py`` fails the build if one is written
out. The rule is structural because the alternative has already failed: the routine runtime
bound has disagreed between the documents and ``config/policy.yaml`` three separate times,
every one of them a second copy that was correct on the day it was typed.

**AND A COUNT IS ONE OF THOSE, WHICH THIS FILE CLAIMED AND THE CHECK COULD NOT SEE.** The
rule looked for digits, so "waiting at {environment}. Any of the nine approvers can release
it" satisfied it while being wrong at one of the two gates it was printed for: nine is the
union of ``admins`` and ``team_leads``, and ``run-approval-admin`` asks two people. A count
is the one bound somebody reaches for a word to write, so the word form was both the
likeliest spelling of the mistake and the only spelling nothing checked. The rule reads
words now and the sentence is derived from the roster for the gate in hand.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from pathlib import Path
from re import findall
from typing import Any, Final, TextIO

from pydantic import ValidationError

from edullm_platform.cli.actions import (
    CANCEL_WORKFLOW,
    PLATFORM_REPOSITORY,
    PRINTED_RUN_ID,
    SUBMIT_WORKFLOW,
    AmbiguousRunIdError,
    GithubUnreachableError,
    PlatformActions,
    RunFacts,
    elapsed_said,
    read_report_sections,
    read_run_facts,
    read_submission_runs,
    report_ceiling_seconds,
)
from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
    find_config_directory,
    load_reviewed_configuration,
)
from edullm_platform.cli.machine import check_document, emit
from edullm_platform.cli.preferences import read_default_team
from edullm_platform.cli.preflight import (
    Preflight,
    Refusal,
    SubmissionRequest,
    first_validation_message,
    resolve_team,
    run_preflight,
    working_tree_refusals,
)
from edullm_platform.cli.presentation import (
    approvers_said,
    plain_decimal,
    render_preflight,
    render_refusals,
    render_run_facts,
    render_run_listing,
)
from edullm_platform.cli.release import (
    installed_version,
    latest_release,
    probe_failed_said,
    staleness_said,
)
from edullm_platform.cli.scaffold import scaffold_spec, workloads_registered_for
from edullm_platform.cli.spec import SPEC_PATH, RunSpec, SpecUnreadableError, find_spec, load_spec
from edullm_platform.cli.workspace import (
    CommandRunner,
    GitFacts,
    SubprocessRunner,
    ToolMissingError,
    github_interop_diagnostic,
    github_login,
    read_git_facts,
)
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.identity import RUN_ID_REGEX

__all__ = [
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_UNREACHABLE",
    "EXIT_UNUSABLE",
    "build_parser",
    "main",
]

#: It stands. Nothing was refused and nothing went wrong.
EXIT_OK: Final = 0

#: Refused on the merits, which is a verdict about the submission or the run id and is the
#: only one of these a submitter can act on by editing something.
EXIT_REFUSED: Final = 1

#: The tool could not be driven, by input or by installation. A flag this verb does not
#: take, a verb that is not built, a configuration that would not load, a missing ``gh``,
#: or a contract this binary broke itself. Retrying it unchanged reaches the same place.
EXIT_UNUSABLE: Final = 2

#: The platform could not be asked. GitHub would not answer, ``gh`` could not dispatch, or
#: the workflow that answers for AWS did not finish. Nobody judged anything, nothing here
#: is anybody's fault, and this is the one of the five worth retrying.
EXIT_UNREACHABLE: Final = 3

#: Interrupted. 128 plus SIGINT, which is what a shell reports for a process killed by one
#: and what a caller already tests for.
EXIT_INTERRUPTED: Final = 130

#: The fewest characters of a run id's UUID this will try to resolve, which is what the
#: listing printed before it printed :data:`~edullm_platform.cli.actions.PRINTED_RUN_ID` of
#: them. Every id copied out of a transcript written before today is this long, and none of
#: them should stop working; below it an abbreviation names most of a week's runs and
#: resolving one costs an artifact download per candidate for an answer nobody wants.
SHORTEST_RUN_ID: Final = 8

#: ``color=False`` on the Pythons that take it, and nothing at all on the ones that do not.
#:
#: **THE ONE PLACE THIS BINARY COULD HAVE WRITTEN AN ANSI ESCAPE, AND IT WOULD NOT HAVE
#: BEEN OURS.** Nothing in this package emits colour, which is what makes a piped run and a
#: terminal run the same bytes and leaves ``NO_COLOR`` with nothing to switch off. Python
#: 3.14 changed that from underneath: ``ArgumentParser`` gained ``color``, it defaults to
#: true, and argparse colourises both ``--help`` and its own error messages whenever
#: ``_colorize.can_colorize`` says the stream can take it. That check is an ``isatty``, so
#: the two runs stop agreeing on exactly the pages a person is likeliest to paste into a
#: message. ``requires-python`` is ``>=3.12`` and ``uv tool install`` fetches whatever is
#: newest where no suitable interpreter exists, so this is not hypothetical and it is not
#: uniform: some researchers would see it and some would not.
#:
#: A dict spread rather than a version branch around ten constructor calls, because the
#: kwarg is a hard ``TypeError`` on 3.12 and 3.13 and this file has to run on all three.
_NO_ARGPARSE_COLOUR: Final[Mapping[str, Any]] = (
    {"color": False} if sys.version_info >= (3, 14) else {}
)


class _PlainHelp(argparse.HelpFormatter):
    """argparse's own help page, wrapped by the rule the rest of this binary wraps by.

    ``HelpFormatter`` fills a description and a flag's help text with ``textwrap``'s
    defaults, which break on hyphens. Every paragraph this package prints itself goes
    through :func:`_wrapped` instead, which turns that off, and the reason is in that
    function's docstring: a ``--fanout-index-parameter`` split across two lines is a flag
    that does not exist, and a path split across two lines is a path nobody can copy. The
    help page is the one place both of those are likeliest and was the one place still
    doing it.
    """

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        return "\n".join(
            textwrap.wrap(
                text,
                width,
                initial_indent=indent,
                subsequent_indent=indent,
                break_on_hyphens=False,
                break_long_words=False,
            )
        )

    def _split_lines(self, text: str, width: int) -> list[str]:
        collapsed = " ".join(text.split())
        return textwrap.wrap(
            collapsed, width, break_on_hyphens=False, break_long_words=False
        )


#: What every parser this file builds is constructed with. One mapping rather than ten
#: argument lists, because a subparser argparse built with the root's settings and one
#: without is a difference nobody sees until a help page is pasted somewhere.
_PARSER_STYLE: Final[Mapping[str, Any]] = {
    "formatter_class": _PlainHelp,
    **_NO_ARGPARSE_COLOUR,
}

#: The five verbs that work, and the line each shows in ``--help`` and in the orientation a
#: bare ``edullm`` prints. One table rather than two so those two can never drift.
BUILT_TODAY: Final = {
    "check": "price a submission here; writes a first spec if there is none",
    "submit": "dispatch the submission workflow",
    "status": "what your runs are doing",
    "logs": "the last lines a run printed",
    "cancel": "stop a run",
}

#: What each built verb does, in the sentence its own ``--help`` opens with.
#:
#: A SECOND TABLE BESIDE ``BUILT_TODAY`` BECAUSE THEY ANSWER TWO QUESTIONS. That one is the
#: line in a list of five, read by somebody choosing a verb, and it has to fit beside four
#: others. This is the paragraph above the flags, read by somebody who has already chosen
#: and wants to know what they are about to run. ``edullm check --help`` printed fifteen
#: flags and never said what ``check`` was for, which made the most-read page in the tool
#: the one page that answered nothing.
#:
#: The unbuilt verbs are not here. Theirs is derived from :data:`NOT_BUILT_YET` in
#: :func:`build_parser_and_verbs`, so a plan cannot be described twice and differently, and
#: the retired names carry theirs in :data:`RETIRED` for the same reason.
WHAT_A_VERB_DOES: Final = {
    "check": (
        "Prices a submission from this working tree, lists every refusal that can be found "
        f"without reaching a network, and writes a first {SPEC_PATH} where a registered "
        "repository has none."
    ),
    "submit": (
        f"Runs the checks check runs and then dispatches {SUBMIT_WORKFLOW}, waiting for the "
        "run id the compile job mints unless --no-wait says not to."
    ),
    "status": (
        "Names your recent submissions, or describes one run and asks AWS about it where "
        "the answer has moved past what GitHub can say. A run it cannot find among the "
        "recent submissions is refused rather than asked about, unless --ask-aws says to."
    ),
    "logs": (
        "Prints the last lines one run printed, read out of the report "
        f"{CANCEL_WORKFLOW} writes when it is asked to look at a run rather than to stop it. "
        "A run it cannot find is refused rather than asked about, unless --ask-aws says to."
    ),
    "cancel": (
        f"Stops one admitted run by dispatching {CANCEL_WORKFLOW} with the reason you give, "
        "which is what the run's history then records instead of a failure."
    ),
}

#: The verbs that are settled and unbuilt, with the sentence each prints. Present so the
#: binary can say "not built yet" rather than "invalid choice", which are different facts:
#: one is a plan and the other is a typo.
#:
#: **NO SENTENCE HERE NAMES A FLAG, AND ``shell``'S DID.** These strings are the ``description``
#: of a real subparser, so ``edullm shell --help`` printed "with --notebook for Jupyter" and
#: then an options list holding ``--config-dir`` and ``--platform-repository`` and nothing
#: else. A plan is an honest thing for a help page to carry and a flag name is not: the
#: options list is the parser's own answer to "what may I type", and a description that
#: contradicts it on the same page is wrong however unbuilt the verb is. The plan is the same
#: plan, said without spelling a flag that does not exist yet.
#:
#: ``RETIRED`` still names ``shell --notebook``, deliberately. That path prints no options
#: list and says in the same paragraph that neither spelling runs today, which is the whole
#: of what this page was missing.
NOT_BUILT_YET: Final = {
    "run": "ship this working tree to a machine and stream the output back",
    "shell": "open an editor over SSH on a machine, or a Jupyter notebook on the same one",
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
            "check writes the spec when a registered repository has none and then prices "
            "it, so the first command a newcomer types is also the one that gets them a "
            "file."
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
    """The parser alone, for callers that do not need to answer for a mistyped flag."""
    return build_parser_and_verbs()[0]


def build_parser_and_verbs() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """The parser, and each verb's own parser beside it.

    THE SECOND HALF EXISTS SO THAT A MISSPELLED FLAG CAN BE ANSWERED WITH THE FLAGS THAT
    VERB TAKES. Every option this binary has lives on a subparser rather than on the root,
    for the reason the comment below gives, and the root parser has no way to reach one:
    ``add_subparsers`` keeps them where only private attributes can find them. Handing them
    back at build time costs a dict and keeps :func:`_nearest_flag` off argparse's
    internals, which is worth more than it looks -- the internals are where a Python
    upgrade breaks a CLI's error path silently, months after anybody last read it.
    """
    parser = argparse.ArgumentParser(
        prog="edullm",
        description=(
            "Submit and follow runs on the eduLLM platform without opening the Actions UI."
        ),
        **_PARSER_STYLE,
    )
    # NAMES THE COMMIT AND NOT ONLY THE VERSION, BECAUSE THE VERSION CANNOT ANSWER THIS
    # ALONE. A release is cut per merge that touches the CLI or the configuration, so two
    # installs made hours apart share a version and carry different config -- and the
    # question somebody asks when a refusal looks wrong is which source their binary was
    # built from. ``release.installed_version`` reads it out of the metadata the installer
    # already wrote, so nothing has to be maintained for this line to stay true.
    parser.add_argument(
        "--version", action="version", version=f"edullm {installed_version().said()}"
    )
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
            "install carries, which is the configuration as it stood at the release this "
            "was installed from rather than as it stands on the platform now. Inside a "
            "platform checkout, --config-dir config is what reads the checkout's."
        ),
    )
    common.add_argument(
        "--platform-repository",
        default=PLATFORM_REPOSITORY,
        help="where the submission workflows live",
    )
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="verb")

    def verb_parser(name: str, description: str) -> argparse.ArgumentParser:
        """One subparser, with the sentence that says what it is for."""
        return verbs.add_parser(
            name,
            parents=[common],
            help=BUILT_TODAY[name],
            description=description,
            **_PARSER_STYLE,
        )

    check = verb_parser("check", WHAT_A_VERB_DOES["check"])
    _add_submission_arguments(check)
    _add_json(check)

    submit = verb_parser("submit", WHAT_A_VERB_DOES["submit"])
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

    status = verb_parser("status", WHAT_A_VERB_DOES["status"])
    status.add_argument("run_id", nargs="?", help="one run; omit for your recent submissions")
    _add_ask_aws(status)

    logs = verb_parser("logs", WHAT_A_VERB_DOES["logs"])
    logs.add_argument("run_id", help="the run to read")
    _add_ask_aws(logs)

    cancel = verb_parser("cancel", WHAT_A_VERB_DOES["cancel"])
    cancel.add_argument("run_id", help="the run to stop")
    cancel.add_argument(
        "--reason",
        required=True,
        help=(
            "why. Recorded on the termination, which is what lets the run's history say it "
            "was cancelled rather than that it broke."
        ),
    )

    built: dict[str, argparse.ArgumentParser] = {
        "check": check,
        "submit": submit,
        "status": status,
        "logs": logs,
        "cancel": cancel,
    }
    for verb, plan in NOT_BUILT_YET.items():
        # THE SENTENCE IS THE PLAN'S, READ RATHER THAN REWRITTEN. An unbuilt verb's help
        # page has one thing to say and it is already written down once; a second wording
        # of it here would be the copy that survives after somebody builds the verb.
        built[verb] = verbs.add_parser(
            verb,
            parents=[common],
            help=f"not built yet: {plan}",
            description=f"Not built yet. It would {plan}.",
            **_PARSER_STYLE,
        )
    return parser, built


def _add_ask_aws(parser: argparse.ArgumentParser) -> None:
    """The opt-in that ``status`` and ``logs`` used to take without asking.

    On the two verbs that only read and not on ``cancel``, which is the whole of the split
    :func:`_unfindable_run_id` argues. ``cancel`` is an instruction rather than a question,
    and refusing to stop a job that turns out to be running is worse than any runner.
    """
    parser.add_argument(
        "--ask-aws",
        action="store_true",
        help=(
            "ask AWS about a run this cannot find among the recent submissions. Dispatches "
            f"{CANCEL_WORKFLOW}, which spends a runner and waits for it. A run this does "
            "find is answered from GitHub either way."
        ),
    )


def _add_json(parser: argparse.ArgumentParser) -> None:
    """The machine-readable form, on the two verbs that are structured under the paragraphs.

    A FLAG AND NOT A TERMINAL CHECK, AND THE DIFFERENCE IS A PROPERTY RATHER THAN A TASTE.
    The agent-facing writing of the last year says emit JSON whenever stdout is not a
    terminal. Doing that would make `edullm check > note.txt` and `edullm check` disagree
    about what was checked, on the one artifact somebody pastes into a message to ask what
    went wrong. The flag is named in this verb's own help instead, which is the answer
    ``docs-frank/reference/designing-the-cli.md`` reaches after weighing both.
    """
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "print one JSON document on stdout instead of the paragraphs, whatever the "
            "outcome. The exit code is unchanged and is still the contract."
        ),
    )


def _no_such_verb(word: str) -> str:
    """A word the binary does not know, answered with the word it does.

    **IT DESCRIBES ``check`` AND NEVER PREDICTS WHAT ``check`` WOULD DO HERE, WHICH IS A
    PROPERTY RATHER THAN A STYLE.** This path judged nothing, so it reads nothing: no git,
    no ``gh``, no reviewed configuration, and ``tests/test_cli_check.py`` asserts the
    absence of every call. A sentence about *this directory* cannot be written from that
    much information -- the version that tried said "here, check would write a first
    .edullm/run.yaml", which is untrue in an unregistered checkout and untrue in a
    directory that is not a checkout at all, and both are where somebody typing a retired
    name is standing. A path that promises nothing cannot break a promise, and the property
    is worth more than the tailored sentence was.
    """
    lines = [""]
    entry = RETIRED.get(word)
    if entry is not None:
        replacement, headline, explanation = entry
        lines += [headline, "", *_wrapped(explanation)]
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


def _orientation() -> str:
    """What a bare ``edullm`` says, which for most people is the first thing it ever says.

    THE SENTENCE ABOUT ``check`` DESCRIBES IT AND DOES NOT PREDICT IT, for the reason
    :func:`_no_such_verb` gives at length: nothing has been read at this point, and the
    tailored version was a promise the binary could not keep in the two directories a
    newcomer is likeliest to be standing in.
    """
    lines = [
        "",
        "edullm submits and follows runs on the eduLLM platform, so that nobody has to",
        "open the Actions UI. These verbs work:",
        "",
    ]
    lines += [f"  {verb:<8} {summary}" for verb, summary in BUILT_TODAY.items()]
    lines += [
        "",
        *_wrapped(
            "Start with check. It prices a submission on this machine and lists every "
            "refusal, reaching no network and dispatching nothing -- and where a "
            f"registered repository has no {SPEC_PATH}, it writes a first one."
        ),
        "",
        "  edullm check --help    the flags one submission takes",
        "",
    ]
    return "\n".join(lines)


def _unusable_arguments(
    unknown: Sequence[str], *, verb: str, parser: argparse.ArgumentParser
) -> str:
    """A flag or a word this verb does not take, answered the way a bad verb is answered.

    ARGPARSE'S ANSWER TO THIS WAS THE ONE MISTAKE IN THE CLI THAT GOT A MENU INSTEAD OF A
    SENTENCE. ``edullm check --experiement pilot`` printed the root usage line -- nine verb
    names, no flags, because the flags are on the subparsers -- so the one piece of
    information the person needed was the one piece the message could not contain. The
    spelling they wanted was one character away.

    The value after a misspelled flag comes back as unknown too, and saying "pilot is not a
    flag" about it would be a second wrong answer, so only the tokens that were typed as
    flags are named.
    """
    flags = tuple(
        dict.fromkeys(token.split("=", 1)[0] for token in unknown if token.startswith("-"))
    )
    lines = [""]
    if flags:
        named = " and ".join(flags)
        is_are = "is not a flag" if len(flags) == 1 else "are not flags"
        lines += _wrapped(f"{named} {is_are} {verb} takes.", indent="")
        near = _nearest_flag(flags[0], parser)
        if near is not None:
            lines += ["", f"Did you mean {near}?"]
    else:
        named = ", ".join(unknown)
        word = "a word" if len(unknown) == 1 else "words"
        lines += _wrapped(f"{verb} was given {word} it does not take: {named}.", indent="")
    lines += ["", f"  edullm {verb} --help    the flags this verb takes", ""]
    return "\n".join(lines)


def _nearest_flag(flag: str, parser: argparse.ArgumentParser) -> str | None:
    """The closest spelling among the flags this verb takes, or nothing when none is close.

    READ OUT OF THE USAGE LINE RATHER THAN OUT OF ``parser._actions``, WHICH IS THE SAME
    INFORMATION FROM A SURFACE THAT IS PROMISED. ``format_usage`` is argparse's own
    rendering of every option the verb has, so the list cannot drift from the parser the
    way a hand-kept table would, and a Python release that rearranges the internals leaves
    this working. The cutoff is the one :func:`_no_such_verb` uses on verbs, so a near miss
    on a flag and a near miss on a verb are near by the same measure.
    """
    options = set(findall(r"--[a-z][a-z0-9-]*", parser.format_usage()))
    near = get_close_matches(flag, sorted(options), n=1, cutoff=0.6)
    return near[0] if near else None


def _wrapped(text: str, *, indent: str = "  ") -> list[str]:
    """Wrapped at spaces and at nothing else, because these paragraphs carry paths.

    ``textwrap`` breaks on hyphens by default, and the paths this prints are full of them --
    a wrapped ``/tmp/pytest-of-frank/...`` comes back as ``pytest-`` and ``of-frank`` on two
    lines, which is a path nobody can copy and one that does not exist. Flag names break
    the same way and matter as much: a ``--fanout-index-parameter`` split across a line is
    a flag that does not exist either.
    """
    return textwrap.wrap(
        text,
        width=78,
        initial_indent=indent,
        subsequent_indent=indent,
        break_on_hyphens=False,
        break_long_words=False,
    )


def _interrupted(dispatched: Sequence[str]) -> str:
    """One line for a Ctrl-C, and the workflow it may have left running without a reader.

    **THE SECOND SENTENCE IS THE WHOLE REASON THIS IS NOT A BARE "interrupted".** Every wait
    this binary makes anybody sit through is after a dispatch, so the moment somebody is
    most likely to reach for Ctrl-C is the moment something is already going. A message
    that stopped at "interrupted" would read as "nothing happened", which is false in the
    one case that costs a runner and a lead's approval. So the workflow is named, and the
    verb that finds out what it did is named beside it.

    Nothing is cancelled on the way out and nothing pretends to be. Stopping a dispatched
    workflow is a second GitHub call this has no business making on a signal, and a message
    that promised it and failed would be worse than the traceback this replaces.
    """
    if not dispatched:
        return "\n".join(
            [
                "",
                *_wrapped(
                    "interrupted. Nothing was dispatched, so nothing is running that this "
                    "started.",
                    indent="",
                ),
                "",
            ]
        )
    named = " and ".join(dict.fromkeys(dispatched))
    return "\n".join(
        [
            "",
            *_wrapped(
                f"interrupted, and {named} was already dispatched. Ctrl-C did not stop it "
                "and nothing here tried to. It is running on GitHub, it will finish on its "
                "own, and edullm status names what it did.",
                indent="",
            ),
            "",
        ]
    )


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

    # BEFORE ANY VERB, BECAUSE IT INVALIDATES ALL OF THEM. A Windows gh on WSL's inherited
    # PATH makes check and submit disagree about who you are and makes status stop finding
    # runs, and both of those read as ordinary answers. Printed rather than refused, and to
    # stderr so it cannot get into anything reading stdout.
    interop = github_interop_diagnostic()
    if interop is not None:
        print("\n".join(_wrapped(interop)), file=stderr)
        print(file=stderr)

    # BEFORE ARGPARSE, BECAUSE ARGPARSE'S ANSWER TO A WORD IT DOES NOT KNOW IS A LIST OF
    # WORDS IT DOES. That is the right answer for a typo and the wrong one for a rename: a
    # researcher who types `dry-run` because a guide said so needs to be told the verb was
    # renamed and what it is now, not handed a menu to search.
    word = tokens[0] if tokens and not tokens[0].startswith("-") else None
    if not tokens:
        print(_orientation(), end="", file=stderr)
        return EXIT_UNUSABLE
    if word is not None and word not in BUILT_TODAY and word not in NOT_BUILT_YET:
        print(_no_such_verb(word), end="", file=stderr)
        return EXIT_UNUSABLE

    parser, verb_parsers = build_parser_and_verbs()
    arguments, unknown = parser.parse_known_args(tokens)
    verb = arguments.verb
    if verb in NOT_BUILT_YET:
        print(
            f"{verb} is not built yet. It is settled -- it would "
            f"{NOT_BUILT_YET[verb]} -- and nothing behind it exists. Built today: "
            f"{', '.join(BUILT_TODAY)}.",
            file=stderr,
        )
        return EXIT_UNUSABLE
    # AFTER THE VERB IS KNOWN TO BE BUILT AND BEFORE ANYTHING READS A FLAG. Argparse's own
    # answer here is the root usage line, which names no flags at all -- they live on the
    # subparsers -- so `edullm check --experiement pilot` printed nine verbs and not one
    # option. Every other mistake this binary can be handed gets a sentence naming what to
    # type instead. ``parse_known_args`` hands the leftovers back rather than exiting on
    # them, which is the whole of what it takes to answer this one the same way.
    if unknown:
        print(
            _unusable_arguments(unknown, verb=verb, parser=verb_parsers[verb]),
            end="",
            file=stderr,
        )
        return EXIT_UNUSABLE
    # HERE RATHER THAN IN THE PREFLIGHT, WHICH IS WHERE IT USED TO BE AND WHAT PUT ONE
    # MISTAKE UNDER TWO EXIT CODES. `--attempts nope` is refused by argparse's `type=int`
    # and exits 2; `--hours nope` was a refusal in the preflight and exited 1, because
    # --hours is parsed by hand -- a bound that went through binary floating point is not
    # the number the approver reads. Two spellings of one mistake answering a script two
    # ways is the thing nobody can write a condition against, so the hand-rolled one is
    # answered in the same class as the one argparse owns, and before any file is read.
    unreadable = _unreadable_hours(getattr(arguments, "hours", None))
    if unreadable is not None:
        print(unreadable, end="", file=stderr)
        return EXIT_UNUSABLE

    # THE LEDGER THE INTERRUPT HANDLER READS, OWNED HERE BECAUSE THE HANDLER IS HERE.
    # Whether a Ctrl-C left something running is the one thing the message below has to get
    # right, and it is a fact each verb learns and then throws away.
    dispatched: list[str] = []
    try:
        if verb == "check":
            return _check(arguments, runner=command_runner, out=stdout, err=stderr, cwd=here)
        if verb == "submit":
            return _submit(
                arguments,
                runner=command_runner,
                out=stdout,
                err=stderr,
                cwd=here,
                dispatched=dispatched,
            )
        if verb == "status":
            return _status(
                arguments,
                runner=command_runner,
                out=stdout,
                err=stderr,
                dispatched=dispatched,
            )
        if verb == "logs":
            return _logs(
                arguments,
                runner=command_runner,
                out=stdout,
                err=stderr,
                dispatched=dispatched,
            )
        if verb == "cancel":
            return _cancel(
                arguments,
                runner=command_runner,
                out=stdout,
                err=stderr,
                dispatched=dispatched,
            )
    except KeyboardInterrupt:
        # CAUGHT BECAUSE IT IS NOT AN ``Exception`` AND SO SLIPPED PAST EVERYTHING BELOW.
        # The handler two blocks down exists so that a researcher never meets a traceback,
        # and it said so in its own comment while Ctrl-C during any wait printed one ending
        # inside ``time.sleep``. It was worst in the one place it was likeliest: ``submit``
        # waits only after it has already dispatched, so the traceback landed over a
        # submission in flight and said nothing about it.
        print(_interrupted(dispatched), end="", file=stderr)
        return EXIT_INTERRUPTED
    except (ConfigurationUnreadableError, SpecUnreadableError, ToolMissingError) as exc:
        print(str(exc), file=stderr)
        return EXIT_UNUSABLE
    except GithubUnreachableError as exc:
        # Never EXIT_REFUSED. GitHub being unreachable says nothing about the submission,
        # and the workflow makes the same separation in its own exit codes. Never
        # EXIT_UNUSABLE either, which is the correction: it shared that code with a
        # mistyped flag, and a caller that wanted to sleep and try again could not tell
        # the one worth retrying from the one that will fail identically forever.
        print(str(exc), file=stderr)
        return EXIT_UNREACHABLE
    except AmbiguousRunIdError as exc:
        # Caught here rather than in each of the three verbs that resolve an id, because
        # the answer does not depend on which one asked: whichever it was cannot act until
        # somebody says which run, and the sentence naming them is the same sentence.
        print(render_refusals([_ambiguous_run_id(exc)]), end="", file=stderr)
        return EXIT_REFUSED
    except ValidationError as exc:
        # THE NET UNDER EVERY CONTRACT MODEL THIS BINARY BUILDS, AND IT IS A NET RATHER THAN
        # A DESIGN. Five constructors sit on the ``check`` path -- ``RunSpec`` in the
        # scaffold, ``RunManifest``, ``FanOut``, ``CostInputs`` and ``RequestFacts`` -- and
        # each of them is a rule this CLI is right to be held to. What must never happen is
        # that being held to one reaches a terminal as a traceback: a researcher who meets
        # one on their first command learns that the tool is broken, which is a more
        # expensive thing to believe than any single defect.
        #
        # So the named cases are refused by name where they arise, and anything that gets
        # past them lands here as EXIT_UNUSABLE -- the exit code for a submission nobody
        # could judge, which is exactly what this is. It says whose fault it is, because a
        # message that reads as a refusal sends somebody to edit a spec that was fine.
        print(
            "edullm assembled something this platform's own contracts refuse, which is a "
            f"defect in edullm rather than in what you typed: {first_validation_message(exc)}. "
            "Nothing was dispatched and nothing was written. Please report it with the "
            f"command you ran, on {PLATFORM_REPOSITORY}.",
            file=stderr,
        )
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
    spec, scaffolded, unscaffoldable = _spec_for_checking(
        arguments, configuration, facts, cwd=cwd
    )
    if scaffolded is not None:
        # READ THE TREE AGAIN, BECAUSE THIS INVOCATION JUST CHANGED IT.
        #
        # The facts above were read before the scaffold was written, so on the one run that
        # writes a file they describe a tree that no longer exists -- and the file is
        # uncommitted, so the very next `check` refuses with `uncommitted_changes` naming
        # it. That refusal is correct. What was wrong is the first run, which said "no
        # refusals" about a working tree it had just made dirty, so the two runs told
        # different stories about the same repository thirty seconds apart and neither
        # reader could tell which one to believe.
        #
        # Re-reading rather than adjusting the facts in place, because the same function
        # answering twice cannot disagree with itself the way a patched copy could. It
        # costs five git calls on the one invocation in a repository's life that writes a
        # spec, and nothing on any other.
        facts = read_git_facts(runner, cwd=cwd)
        # ON STDERR UNDER --json, BECAUSE STDOUT IS ONE DOCUMENT THERE AND NOTHING ELSE.
        # A caller pipes stdout into a parser, and the one invocation in a repository's life
        # that writes a spec is exactly the one a first-run agent makes.
        wrote = err if arguments.json else out
        print(_scaffolded_said(scaffolded, facts), file=wrote)
        print(file=wrote)
    preflight = _preflight(
        arguments, configuration, facts, spec, submitter, unscaffoldable=unscaffoldable
    )
    if arguments.json:
        emit(
            check_document(preflight, configuration=configuration, submitter=submitter),
            out=out,
        )
    else:
        print(render_preflight(preflight, configuration=configuration), end="", file=out)
    return EXIT_REFUSED if preflight.refused else EXIT_OK


def _scaffolded_said(written: Path, facts: GitFacts) -> str:
    """Where the file went, and that it is the change the refusal below is about.

    The connection is not obvious from the two lines on their own. ``git status`` collapses
    a wholly untracked directory to a single entry, so a scaffold into a repository with no
    ``.edullm/`` is reported as ``.edullm/`` while this line names ``.edullm/run.yaml`` --
    and a reader who does not join them up reads the refusal as being about something else
    they have forgotten to commit.
    """
    if not _named_by_the_dirty_tree(facts, written):
        return f"wrote {written}"
    return "\n".join(
        [
            f"wrote {written}",
            *_wrapped(
                "It is not in any commit yet, which is what the uncommitted_changes "
                "refusal below is naming. Commit it and check clears that one."
            ),
        ]
    )


def _named_by_the_dirty_tree(facts: GitFacts, written: Path) -> bool:
    """Whether ``uncommitted_changes`` is about to name the file that was just written."""
    if facts.root is None:
        return False
    try:
        relative = written.relative_to(facts.root).as_posix()
    except ValueError:
        return False
    return any(
        relative == entry or relative.startswith(entry if entry.endswith("/") else f"{entry}/")
        for entry in facts.dirty_paths
    )


def _spec_for_checking(
    arguments: argparse.Namespace,
    configuration: ReviewedConfiguration,
    facts: GitFacts,
    *,
    cwd: Path,
) -> tuple[RunSpec | None, Path | None, Refusal | None]:
    """``check`` absorbing ``new``: a repository with no spec gets one, then gets checked.

    Written rather than offered, because the alternative is a prompt and a prompt is what
    stops an agent driving this. What makes writing safe is that everything in the file is
    either read from the catalog or is the reviewed default the form itself carries, and
    the check that follows immediately says which of them will not do.

    The third answer is the one that was missing. Where nothing can be written, that is a
    fact about where somebody is standing and it is theirs to hear -- not a constructor's
    to raise. ``arguments.workload or None`` and ``arguments.compute or None`` are the same
    point one layer down: ``--workload ""`` is an empty flag rather than a profile named
    "", and everywhere else in this file an empty override already reads as absent.
    """
    declared = arguments.spec if getattr(arguments, "spec", None) else None
    if declared is not None:
        return load_spec(declared), None, None
    found = find_spec(cwd)
    if found is not None:
        return load_spec(found), None, None
    if facts.root is None or facts.repository is None:
        return None, None, None
    unscaffoldable = _nothing_to_scaffold(arguments, configuration, repository=facts.repository)
    if unscaffoldable is not None:
        return None, None, unscaffoldable
    written = scaffold_spec(
        configuration,
        repository=facts.repository,
        root=facts.root,
        workload_profile=arguments.workload or None,
        compute_profile=arguments.compute or None,
    )
    return load_spec(written), written, None


def _nothing_to_scaffold(
    arguments: argparse.Namespace, configuration: ReviewedConfiguration, *, repository: str
) -> Refusal | None:
    """Why no first spec can be written here, in the order the compile job asks it.

    **THE REGISTRY IS ASKED BEFORE THE CATALOG, WHICH IS ``run_preflight``'S ORDER AND
    ``compile_submission``'S BEFORE IT.** Both say why in their own words: a refusal naming
    a workload profile when the real problem is an unregistered repository points at a
    field that was never what stood in the way. It also decides the more visible thing --
    an unregistered repository gets no file written into it, where before this it got one
    whenever the catalog happened to name a workload for it, which ``dolma`` does.

    **AND IT IS ASKED HERE RATHER THAN AFTER THE WRITE, BECAUSE THE ALTERNATIVE IS A
    TRACEBACK.** ``scaffold_spec`` picks the empty string for a workload it cannot infer,
    ``RunSpec`` refuses that on ``min_length=1``, and the ``ValidationError`` came out of
    ``main`` unhandled -- so the first command anybody typed in an unregistered checkout,
    which is the likeliest place to try one, answered with a stack trace ending in "String
    should have at least 1 character".
    """
    if not configuration.repositories.is_registered(repository):
        registered = ", ".join(
            sorted(entry.repository for entry in configuration.repositories.repositories)
        )
        return Refusal(
            code="unregistered_repository",
            detail=(
                f"{repository!r} is not a repository config/repositories.yaml carries, so "
                f"there is nothing here to submit and no {SPEC_PATH} was written. A "
                "registration is what gives a repository somewhere to publish an image "
                "from and a workload profile a submission can name. It is not a change to "
                "make yourself: it lands across the registry, the workload catalog, the "
                "submission form, an ECR stack and an IAM role no workflow may deploy. Ask "
                f"for it by opening an issue on {PLATFORM_REPOSITORY} -- edullm add is the "
                "verb that will eventually do this and is not built yet. Registered today: "
                f"{registered}."
            ),
        )
    if arguments.workload or workloads_registered_for(configuration, repository):
        return None
    return Refusal(
        code="no_workload_profile_registered",
        detail=(
            f"{repository!r} is registered and config/workload-catalog.yaml names no workload "
            f"profile for it, so a first {SPEC_PATH} would have nothing to point at. A "
            "workload profile fixes the runtime bound, the attempt bound and the checkpoint "
            "contract for one codebase, so adding one is a pull request against the "
            "platform -- and until there is one, no run can name this repository at all."
        ),
    )


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
    dispatched: list[str],
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

    actions = PlatformActions(
        runner, repository=arguments.platform_repository, dispatched=dispatched
    )
    if not arguments.team:
        _say_where_the_team_came_from(preflight, err=err)
    _say_whether_this_edullm_is_current(
        runner, repository=arguments.platform_repository, err=err
    )
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
        print("\n".join(_wrapped(_waiting_said(environment, configuration), indent="")), file=out)
    return EXIT_OK


def _waiting_said(environment: str, configuration: ReviewedConfiguration) -> str:
    """Which gate holds this run, and how many people the roster says can release it.

    THE COUNT IS DERIVED FROM THE ROSTER RATHER THAN WRITTEN, WHICH IT WAS NOT. This said
    "any of the nine approvers can release it" whatever the gate. Nine is the union of
    ``admins`` and ``team_leads`` and so happened to describe the lead gate; at
    ``run-approval-admin`` two people can release and the sentence was wrong by seven, in
    front of the submitter of the most expensive runs this platform takes.

    The environment arrives as whatever the compile job wrote into the artifact, so it is
    matched against the enum rather than trusted. A gate this binary does not recognise is
    named and not counted: a count against the wrong gate is the defect being fixed here,
    and inventing one for an unknown name would be the same mistake with a different cause.
    """
    try:
        gate = ApprovalEnvironment(environment)
    except ValueError:
        return f"waiting at {environment or 'an approval gate'}."
    return f"waiting at {gate.value}. {approvers_said(configuration.inventory, gate)}"


def _say_where_the_team_came_from(preflight: Preflight, *, err: TextIO) -> None:
    """The team and its origin, on a submit that did not type one.

    **THE ONLY ORIGIN THAT LEAVES NO TRACE IS THE ONE THIS EXISTS FOR.** A ``--team`` is in
    the command itself, so a transcript six weeks old still says who chose it. A team that
    came out of a personal default is in a file on one laptop, and a transcript of the
    submission that spent the money would otherwise show a group nobody in it typed and no
    way to tell where it came from. The roster's own answer is printed beside it rather than
    suppressed, because the reader's question is "where did this team come from" and "from
    the roster" is an answer to it.

    On stderr with the staleness line, because it is not the answer anybody asked for, and
    before the dispatch rather than after it, because a person who reads it and disagrees
    still has the moment before a runner starts. It cannot refuse and it cannot wait.
    """
    if not preflight.team_source or not preflight.request.team:
        return
    said = f"charging this to {preflight.request.team}, {preflight.team_source}."
    print("\n".join(_wrapped(said, indent="")), file=err)


def _say_whether_this_edullm_is_current(
    runner: CommandRunner, *, repository: str, err: TextIO
) -> None:
    """One ``gh api`` call, in ``submit`` and nowhere else, that cannot stop a submission.

    **IN ``submit`` ONLY, BECAUSE ``check`` IS MEASURED AND THE MEASUREMENT IS A FEATURE.**
    ``check`` answers in 0.18 s and reaches no network, which is what makes it the verb
    somebody runs half a dozen times while editing a spec and the verb that works on a
    cluster login node with no egress. One API call would be a tenth of a second on a good
    connection and a hang on a bad one, spent on a question that only matters at the moment
    a submission costs somebody's approval. So it is asked once, here, immediately before
    the dispatch.

    **AND IT NEVER BLOCKS.** A failed probe, a timeout, a repository with no releases and
    an offline laptop all reach the same place: a line on stderr and a dispatch. A
    validator that stops working on a train is worse than one that is occasionally stale,
    and ``GithubUnreachableError`` already makes exactly this separation for the calls that
    do matter -- GitHub being unreachable says nothing about the submission.
    """
    latest = latest_release(runner, repository=repository)
    warning = staleness_said(installed_version(), latest, repository=repository)
    if warning is not None:
        for paragraph in warning.split("\n"):
            print("\n".join(_wrapped(paragraph)) if paragraph[:1] != " " else paragraph, file=err)
        print(file=err)
        return
    failed = probe_failed_said(latest)
    if failed is not None:
        print("\n".join(_wrapped(failed)), file=err)
        print(file=err)


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
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    dispatched: list[str],
) -> int:
    actions = PlatformActions(
        runner, repository=arguments.platform_repository, dispatched=dispatched
    )
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

    _said_resolving(arguments.run_id, err)
    facts = read_run_facts(actions, arguments.run_id)
    if not facts.was_found and not arguments.ask_aws:
        print(render_refusals([_unfindable_run_id(facts)]), end="", file=err)
        return EXIT_REFUSED
    print(render_run_facts(facts), end="", file=out)
    if not facts.needs_a_dispatch:
        return EXIT_OK
    print(file=out)
    # ``facts.run_id`` and not what was typed, for all three of these verbs. An abbreviation
    # resolved a moment ago, and the workflow being dispatched knows the whole id and writes
    # the whole id into the report this then reads headings out of.
    return _drive_the_run_report(
        actions,
        run_id=facts.run_id,
        stop=False,
        reason=None,
        headings=(facts.run_id, "Runs submitted by", "No runs found"),
        out=out,
        err=err,
    )


def _logs(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    dispatched: list[str],
) -> int:
    refusal = _malformed_run_id(arguments.run_id)
    if refusal is not None:
        print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED
    actions = PlatformActions(
        runner, repository=arguments.platform_repository, dispatched=dispatched
    )
    _said_resolving(arguments.run_id, err)
    facts = read_run_facts(actions, arguments.run_id)
    if not facts.was_found and not arguments.ask_aws:
        print(render_refusals([_unfindable_run_id(facts)]), end="", file=err)
        return EXIT_REFUSED
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
        run_id=facts.run_id,
        stop=False,
        reason=None,
        headings=("The last lines this run printed",),
        because=facts.because,
        out=out,
        err=err,
    )


def _cancel(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    dispatched: list[str],
) -> int:
    refusal = _malformed_run_id(arguments.run_id)
    if refusal is not None:
        print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED
    actions = PlatformActions(
        runner, repository=arguments.platform_repository, dispatched=dispatched
    )
    _said_resolving(arguments.run_id, err)
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
        #
        # ON STDERR, WITH THE OTHER TWO REFUSALS THIS VERB CAN PRINT. It used to go to
        # stdout while a malformed run id and an ambiguous one went to stderr, so a script
        # reading one verb's refusals had to read both streams and could not tell from the
        # code which one to look in. The rule the rest of the binary already follows is
        # ``check``'s against ``submit``'s: a refusal is on stdout where the list of
        # refusals is the answer that was asked for, and on stderr where it is why the
        # command failed. Nothing was stopped here, so it is the second one.
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
            file=err,
        )
        return EXIT_REFUSED
    return _drive_the_run_report(
        actions,
        run_id=facts.run_id,
        stop=True,
        reason=arguments.reason,
        headings=("Run stopped", facts.run_id),
        because=facts.because,
        out=out,
        err=err,
    )


def _because(facts: RunFacts) -> str:
    """The one-line explanation, wrapped the way every other paragraph here is."""
    return "\n".join(textwrap.wrap(facts.because, width=78)) + "\n"


def _ceiling_said() -> str:
    """How long a dispatch-and-read can run, rounded to the minute a reader would use.

    Read off :func:`report_ceiling_seconds` rather than written out, for the reason
    ``tests/test_cli_no_hardcoded_bounds.py`` fails the build over: a duration typed into a
    sentence is correct on the day it is typed and stays that way after somebody widens the
    poll it describes. Rounded up, because the promise a ceiling makes is an upper one.
    """
    minutes = -(-int(report_ceiling_seconds()) // 60)
    return f"{minutes} minutes" if minutes != 1 else f"{minutes} minute"


class _SignOfLife:
    """A whole line on stderr once a minute while a wait runs, and nothing in between.

    **NOT A SPINNER, AND THE DIFFERENCE IS THE PROPERTY THIS BINARY IS BUILT ON.** A spinner
    is a carriage return and a cursor move, so a run piped into a file stops being the run a
    terminal showed and a pasted transcript stops being what the next person sees. This
    writes ordinary lines, in order, with no escape in them, so the bytes are the same
    either way and there is still nothing for ``NO_COLOR`` to switch off.

    On stderr, with the sentence that announced the wait, because it is not part of the
    answer. A script reading a run's log tail off stdout gets the log tail.

    A class rather than a closure so that the caller can read :attr:`elapsed` back. The two
    polls behind one dispatch are two loops, and a clock that restarted between them would
    print "1 minute so far" twice on a wait that had run for six.
    """

    def __init__(self, err: TextIO, *, every: float = 60.0) -> None:
        self._err = err
        self._every = every
        self._said_at = 0.0
        self.elapsed = 0.0

    def __call__(self, elapsed: float) -> None:
        self.elapsed = elapsed
        if elapsed - self._said_at < self._every:
            return
        self._said_at = elapsed
        print(f"still waiting, {elapsed_said(_ago(elapsed))} so far.", file=self._err)


def _ago(seconds: float) -> datetime:
    """The instant that many seconds back, so :func:`elapsed_said` can name the gap.

    Going through a pair of instants rather than formatting the seconds here, because
    ``elapsed_said`` is where this repository decides that a wait reads as ``38s`` under a
    minute and ``1h11m`` over an hour, and a second opinion about that would show up as two
    verbs disagreeing about what four minutes is called.
    """
    return datetime.now(UTC) - timedelta(seconds=seconds)


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

    **AND HOW SLOW IT CAN GET IS SAID TOO, WHICH IT WAS NOT.** The sentence promised tens of
    seconds and the two polls behind it allow eleven minutes between them, so the one
    invocation that ran long was the one whose only explanation was a lie. Nothing here
    streams and nothing here is going to in this window; what an eleven minute worst case
    needs instead is a reader who was told the ceiling before it started and is shown a
    line a minute while it runs. Both numbers are read out of the poll parameters rather
    than typed, because a number typed into a sentence is the copy nobody edits.
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
        "\n".join(
            _wrapped(
                f"asking AWS about {run_id}. This dispatches {CANCEL_WORKFLOW}, which holds "
                "the only identity that may read a Batch job, so it waits first for a "
                "runner and then for that workflow to finish. Usually well under a minute, "
                f"and it gives up after {_ceiling_said()}. A line every minute says it is "
                "still waiting.",
                indent="",
            )
        ),
        file=err,
    )
    actions.dispatch(CANCEL_WORKFLOW, fields)
    waiting = _SignOfLife(err)
    run = actions.wait_for_a_new_run(
        CANCEL_WORKFLOW, actor=None, after=dispatched_at, waiting=waiting
    )
    if run is None:
        # NOT EXIT_UNUSABLE, WHICH IT WAS. Nothing about this is the caller's doing and
        # running it again is the reasonable next move, which is the whole distinction
        # between 2 and 3.
        print(
            "dispatched, and the workflow run it started could not be found within the "
            f"poll window. It is running; the {CANCEL_WORKFLOW} page carries its answer.",
            file=err,
        )
        return EXIT_UNREACHABLE
    identifier = int(run["id"])
    conclusion = actions.wait_for_completion(
        identifier, waiting=waiting, elapsed_already=waiting.elapsed
    )
    report = read_report_sections(actions.job_log(identifier), headings)
    if report:
        print(report, file=out)
    else:
        print(
            f"the workflow finished {conclusion} and its report named no section this verb "
            f"reads. The whole of it is at {run.get('html_url')}.",
            file=err,
        )
    # **NOT EXIT_REFUSED, WHICH IS THE ONE THAT WAS ACTIVELY MISLEADING.** This is the
    # reporting workflow's own conclusion, and it ends up here behind ``logs`` and
    # ``status``, neither of which refuses anything at all. A reporting job that failed for
    # its own reasons told a script a submission had been declined, which is a sentence
    # about the submission and is false. What actually happened is that the platform could
    # not be asked, so it lands with everything else that could not be asked.
    return EXIT_OK if conclusion == "success" else EXIT_UNREACHABLE


def _malformed_run_id(run_id: str) -> Refusal | None:
    """The same shape ``cancel-run.yml`` checks before it touches AWS, checked before that.

    That workflow refuses a malformed id in its first step, deliberately, because a mistake
    is a thing to answer in the workflow rather than a call to make with a credential in
    hand. The same argument one layer out is stronger: here it costs no runner either.

    **AN ABBREVIATION IS A RUN ID HERE, WHICH THE WORKFLOW WILL NEVER SEE.** The listing
    prints a shortened id, the shortened id is what people copy, and refusing what the tool
    itself printed made the one remedy this refusal offers -- run the listing -- a circle.
    Anything from the eight characters the listing used to print up to the whole id is
    taken, and resolved against the recent submissions before anything is dispatched;
    ``cancel-run.yml`` is still handed the whole id, because by then there is one.
    """
    if RUN_ID_REGEX.fullmatch(run_id) or _abbreviates_a_run_id(run_id):
        return None
    return Refusal(
        code="run_id_not_well_formed",
        detail=(
            f"{run_id!r} is not a run id. One reads run_ followed by a UUID, and the "
            f"leading {SHORTEST_RUN_ID} characters of that UUID are enough as long as no "
            "two of your recent runs share them. edullm status with no argument lists "
            "yours in the short form."
        ),
    )


def _unfindable_run_id(facts: RunFacts) -> Refusal:
    """A well-formed run id no recent dispatch carries, refused rather than guessed at.

    **THIS IS THE ONE PATH IN THE BINARY THAT SPENT MONEY ON A GUESS.** ``status`` and
    ``logs`` read ``UNSURE`` as "ask AWS", and a run id that cannot be found reads
    ``UNSURE``. So pasting an id out of a transcript older than the artifact window into a
    verb that looks read-only dispatched ``cancel-run.yml``, waited for a runner, and could
    sit for the whole poll ceiling before answering about a run that finished last month. A
    malformed id was refused for free, so the failure was specific to ids that look right,
    which is every id anybody actually pastes.

    **AND "NOT FOUND" IS NOT "DOES NOT EXIST", WHICH IS WHY THIS IS WORDED AS IT IS.** The
    window is bounded and artifacts expire, so a real run that ran a month ago is not
    joinable to a dispatch and is indistinguishable here from an id that was never minted.
    Nothing local can tell those apart. What the refusal can do is say so rather than pick
    one, name the window it did search, and name the flag that pays for the certain answer.
    A reader gets three separable outcomes and a script gets three of them too: exit 1 with
    ``run_id_not_well_formed`` for a shape that is wrong, exit 1 with ``run_id_not_found``
    for a shape that is right and a run that is not here, and exit 0 with the run's facts
    when it is.

    **``cancel`` IS NOT GIVEN THIS AND MUST NOT BE.** The argument that put the old
    behaviour there is correct for exactly one verb: refusing to stop a job that turns out
    to be running is far worse than a wasted runner, and an id the window does not reach may
    still name something burning a GPU. ``cancel`` is an instruction and pays the runner
    every time. ``status`` and ``logs`` are questions, and the cheap wrong answer to a
    question is a refusal that names the flag, not the whole poll ceiling in silence.
    """
    return Refusal(
        code="run_id_not_found",
        detail=(
            f"{facts.because} Whether this one is older than the window or was never minted "
            "at all is not something that can be told from here, and this does not guess: it "
            "may well be a real run. Nothing was dispatched and nothing was waited for. Pass "
            f"--ask-aws to ask anyway, which dispatches {CANCEL_WORKFLOW} for the one "
            f"identity that may read a Batch job, spends a runner and gives up after "
            f"{_ceiling_said()}."
        ),
    )


def _said_resolving(run_id: str, err: TextIO) -> None:
    """Name the wait an abbreviation costs, before paying it rather than after.

    A whole id is usually found in the first one or two manifests read, and an abbreviation
    cannot stop there -- it has to read the window out to know no second run answers to it,
    which measured 26 seconds against the real platform. This CLI says what a wait is for
    everywhere else it makes somebody wait, and 26 seconds of a silent terminal is how a
    person learns to stop pasting the short form.
    """
    if RUN_ID_REGEX.fullmatch(run_id):
        return
    print(
        "\n".join(
            _wrapped(
                f"resolving {run_id}. Nothing indexes run ids, so this reads the manifest "
                "of each recent submission until it knows which one -- a few seconds. An "
                "id given in full is found in the first one or two.",
                indent="",
            )
        ),
        file=err,
    )


def _abbreviates_a_run_id(given: str) -> bool:
    """Whether this is the beginning of some well-formed run id and enough of one.

    ASKED BY COMPLETING IT RATHER THAN BY A SECOND REGEX, so there is still exactly one
    statement in this codebase of what a run id looks like. A string is the start of a run
    id when filling the rest in from a valid one leaves something ``RUN_ID_REGEX`` accepts,
    which gets the version and variant nibbles checked for free at whatever position they
    fall in -- and a second pattern drifting from the first is how a CLI ends up accepting
    an id the workflow it feeds will reject.
    """
    # Filled with a hex letter and not with zeros, which ``test_evidence`` reads as an AWS
    # account id -- correctly, since twelve digits in a row in this tree usually is one.
    whole = "run_aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    if not len("run_") + SHORTEST_RUN_ID <= len(given) <= len(whole):
        return False
    return RUN_ID_REGEX.fullmatch(given + whole[len(given) :]) is not None


def _ambiguous_run_id(error: AmbiguousRunIdError, *, now: datetime | None = None) -> Refusal:
    """More than one recent run begins that way, answered with which ones and no more.

    **THE REMEDY IS IN THE SENTENCE AND NOT IN ANOTHER COMMAND**, which is the whole design
    of this one. The abbreviation being refused was copied out of ``edullm status``, so
    "run edullm status" is a remedy that hands back the input that failed; a refusal whose
    cure is the thing that caused it is worse than a refusal with no cure, because the
    second at least does not waste a minute. Every match is named at a length that tells it
    from the others, with its experiment and how long ago it went in -- because ids this
    close together are ids minted seconds apart, and the clock is the only thing about them
    a person remembers.
    """
    matched = tuple(match.run_id or "" for match in error.matches)
    distinguishing = _shortest_distinguishing(matched)
    # THE CLOCK TIME AS WELL AS THE ELAPSED ONE, WHICH IS THE OPPOSITE OF WHAT THE LISTING
    # DOES AND RIGHT HERE. Runs whose ids collide were minted seconds apart, so "45m ago"
    # is the same sentence twice -- the one form that cannot separate them is the one this
    # refusal exists to separate them by.
    said = " and ".join(
        f"{distinguishing[match.run_id or '']} ({match.experiment or 'no experiment named'}, "
        f"submitted {elapsed_said(match.created_at, now=now)} ago at "
        f"{match.created_at.astimezone(UTC).strftime('%H:%M:%S')} UTC)"
        for match in error.matches
    )
    return Refusal(
        code="run_id_is_ambiguous",
        detail=(
            f"{error.given!r} is the beginning of {len(error.matches)} of your recent run "
            f"ids, so this cannot tell which run you mean: {said}. Pass one of those longer "
            "forms."
        ),
    )


def _shortest_distinguishing(run_ids: Sequence[str]) -> dict[str, str]:
    """Each id at the length the listing prints, or longer where that still does not part them.

    Two runs a minute apart differ somewhere in the middle of the timestamp, so printing
    them whole would put two 41-character strings in one sentence and make the reader diff
    them. Starting at the printed length rather than at the shortest that works means the
    forms named here are usually the exact strings ``edullm status`` printed, which is what
    lets somebody recognise their run instead of parsing it.
    """
    cut = len("run_") + PRINTED_RUN_ID
    while cut < max(len(run_id) for run_id in run_ids):
        if len({run_id[:cut] for run_id in run_ids}) == len(set(run_ids)):
            break
        cut += 1
    return {run_id: run_id[:cut] for run_id in run_ids}


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
    *,
    unscaffoldable: Refusal | None = None,
) -> Preflight:
    """Merge the spec, the flags and the working tree, then run every local check.

    Where a value can come from two places the flag wins, and the spec is the default. That
    is the direction ``system-overview.md`` sets for the machine -- a suggestion in version
    control, a decision at submit time -- and applying the same rule to the workload keeps
    a submitter from having to edit a committed file to try a different profile once.

    ``unscaffoldable`` replaces the generic refusal rather than joining it. Both answer the
    same question and the specific one answers it better, and a reader handed two refusals
    that are one problem under two spellings goes looking for the second problem -- which
    is the argument ``run_preflight`` already makes about the conditions it deduplicates.
    """
    refusals: list[Refusal] = working_tree_refusals(facts)
    if unscaffoldable is not None:
        refusals.append(unscaffoldable)
    elif spec is None:
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
        else resolve_team(configuration, submitter=submitter, default=read_default_team())
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
        maximum_runtime_hours=_decimal_hours(arguments.hours),
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


def _decimal_hours(text: str | None) -> Decimal | None:
    """``--hours`` as base-ten text, which is the shape the whole path carries it in.

    The workflow's own comment says why: a bound that went through binary floating point is
    not the number the approver reads. So it is parsed as a decimal here and formatted back
    to text before it reaches the form, and never becomes a float on the way.

    Nothing for a value nobody can read, and no refusal either. :func:`_unreadable_hours`
    answers that one in ``main``, before any of this runs, in the class argparse puts the
    same mistake on ``--attempts`` into.
    """
    if text is None:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _unreadable_hours(text: str | None) -> str | None:
    """The sentence for a ``--hours`` that is not a number, or nothing where it is one.

    ARGPARSE'S OWN ANSWER TO THE SAME MISTAKE ON ``--attempts`` IS ONE UNWRAPPED LINE, AND
    THIS IS NOT THAT ON PURPOSE. Matching its exit code is what a caller needs; matching
    its brevity would throw away the two things a person needs, which are that a fraction
    is allowed and what lowering the bound is good for. The code is the contract and the
    prose is the courtesy, and they are not the same promise.
    """
    if text is None or _decimal_hours(text) is not None:
        return None
    return "\n".join(
        [
            "",
            *_wrapped(
                f"--hours takes a positive base-ten number of hours and was given {text!r}, "
                "so nothing was read and nothing was dispatched. Fractions are fine, and "
                "0.5 is thirty minutes. The bound is what the worst case multiplies, so "
                "lowering it is what moves a short run under the automatic bound.",
                indent="",
            ),
            "",
        ]
    )
