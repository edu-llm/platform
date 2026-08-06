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
``BUILT_TODAY`` is every verb ``docs-frank/reference/decisions.md`` settled on 2026-08-04
and runs: the governed-submission core of ``check``, ``submit``, ``status``, ``logs`` and
``cancel``, the two that file something with ``add`` and ``ask``, and the two ungated ones
with ``run`` and ``shell``. ``NOT_BUILT_YET`` is empty as of the exploration route and stays
because the next settled-and-unbuilt name has somewhere to be declared, which is what makes
the answer to it a plan rather than a usage error. ``RETIRED`` is the names that were folded
into those: ``dry-run`` and ``new`` into ``check``, ``activity`` into bare ``status``,
``notebook`` into ``shell --notebook``, and ``results`` into looking at Weights and Biases.
A typo is the fourth case and gets the nearest spelling and the list.

TWO OF THOSE NINE ARE UNGATED AND THAT IS THE DESIGN RATHER THAN AN OMISSION. ``run`` and
``shell`` call no part of ``run_preflight``: nothing they do is recorded, approved or citable,
so the refusals that protect a record have nothing to protect here. ``cli/lane.py``'s header
carries the argument and ``tests/test_lane_verdicts.py`` fails if either verb ever reaches
that path. What they refuse instead is a closed set of four, all of them about a destination
being unspellable rather than a permission being withheld.

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
import json
import os
import platform
import shlex
import shutil
import sys
import textwrap
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from pathlib import Path
from re import findall
from typing import Any, Final, TextIO

from pydantic import ValidationError

from edullm_platform.cli.actions import (
    CANCEL_WORKFLOW,
    EDULLM_VERSION_FIELD,
    PLATFORM_REPOSITORY,
    PRINTED_RUN_ID,
    REGISTER_WORKFLOW,
    SUBMIT_WORKFLOW,
    AmbiguousRunIdError,
    GithubUnreachableError,
    PlatformActions,
    RunFacts,
    elapsed_said,
    read_report_sections,
    read_run_facts,
    read_submission_runs,
    registration_compare_url,
    report_ceiling_seconds,
    submit_ceiling_seconds,
)
from edullm_platform.cli.configuration import (
    ConfigurationUnreadableError,
    ReviewedConfiguration,
    find_config_directory,
    load_reviewed_configuration,
)
from edullm_platform.cli.intake import (
    ADD_KINDS,
    ASK_KINDS,
    ASK_QUEUE_LABEL,
    SELF_SERVICE_KINDS,
    issue_body,
    register_repository_form,
    routed_to_ask,
)
from edullm_platform.cli.lane import (
    AWS_BROKER,
    AWS_LOGIN_COMMAND,
    AWS_PROFILE_VARIABLE,
    GPU_AMI_PARAMETER,
    PLATFORM_NETWORK_NAME,
    SESSION_PLUGIN,
    LaneExpiry,
    LaneRequest,
    WorkingTierSettings,
    ZoneAttempt,
    agent_online_argv,
    another_zone_may_answer,
    assume_lane_argv,
    aws_config_path,
    carry_back_script,
    command_line,
    command_not_found_said,
    credentials_environment,
    default_compute_profile,
    expiry_for_a_new_machine,
    find_lane_machines_argv,
    find_machine_argv,
    find_subnets_argv,
    instance_type_for,
    lane_machines,
    lane_refusals,
    lane_subnets,
    load_working_tier_settings,
    machine_already_running,
    machine_for_project,
    missing_broker_refusal,
    missing_plugin_refusal,
    no_machine_to_stop,
    no_zone_had_this_shape,
    notebook_forward_argv,
    person_from_caller_arn,
    placement_said,
    placement_verdict,
    placement_warning,
    plugin_install_commands,
    priced_as,
    read_aws_config,
    refusal_code,
    remote_command_argv,
    remote_script,
    resolve_aws_profile,
    run_instances_argv,
    shell_session_argv,
    ssh_proxy_command,
    subnets_to_try,
    terminate_argv,
    what_stopping_did,
    what_the_machine_carries,
    whose_machine_refusals,
    work_directory,
    working_uri,
    zones_offering,
    zones_offering_argv,
)
from edullm_platform.cli.machine import (
    check_document,
    emit,
    envelope,
    refusal_document,
    status_document,
    status_listing_document,
)
from edullm_platform.cli.preferences import read_default_team
from edullm_platform.cli.preflight import (
    Preflight,
    Refusal,
    SubmissionRequest,
    first_validation_message,
    price_what_is_known,
    resolve_team,
    run_preflight,
    said_once,
    untracked_the_image_will_not_see,
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
from edullm_platform.cli.spec import (
    SPEC_DIRECTORY,
    SPEC_PATH,
    RunSpec,
    SpecUnreadableError,
    find_spec,
    load_spec,
)
from edullm_platform.cli.studio import (
    STUDIO_CONFIG_FILE,
    RunningApp,
    StudioRequest,
    StudioSettings,
    StudioShape,
    already_running_said,
    could_not_resolve_the_image,
    create_app_argv,
    create_space_argv,
    create_user_profile_argv,
    delete_app_argv,
    describe_app_argv,
    describe_space_argv,
    describe_user_profile_argv,
    image_account_argv,
    image_arn_for,
    load_studio_settings,
    nothing_to_stop,
    presigned_url_argv,
    price_said,
    running_app,
    shape_for,
    studio_document,
    studio_name_for,
    studio_refusals,
    unpriced_shape,
    unstopped_said,
)
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
from edullm_platform.contracts.repository_registry import UnknownRepositoryError
from edullm_platform.researcher_lane import load_lane_settings

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
        return textwrap.wrap(collapsed, width, break_on_hyphens=False, break_long_words=False)


#: What every parser this file builds is constructed with. One mapping rather than ten
#: argument lists, because a subparser argparse built with the root's settings and one
#: without is a difference nobody sees until a help page is pasted somewhere.
_PARSER_STYLE: Final[Mapping[str, Any]] = {
    "formatter_class": _PlainHelp,
    **_NO_ARGPARSE_COLOUR,
}

#: Every verb that works, and the line each shows in ``--help`` and in the orientation a
#: bare ``edullm`` prints. One table rather than two so those two can never drift.
#:
#: This said five until 2026-08-06 and had since the exploration route landed four more. The
#: table below is the only thing that knows how many there are, so a comment above it saying
#: so is a copy of it that nothing compares, which is the argument
#: ``tests/test_cli_no_hardcoded_bounds.py`` now makes structurally.
BUILT_TODAY: Final = {
    "check": "price a submission here, and write a first spec if there is none",
    "submit": "dispatch the submission workflow",
    "status": "what your runs are doing",
    "logs": "the last lines a run printed",
    "cancel": "stop a run",
    "add": "teach the platform about a repository, dataset, shape, model or person",
    "ask": "file an ask that a person answers",
    "run": "ship this working tree to a machine and stream the output back",
    "shell": "a terminal on a machine of your own, or a notebook on it",
    "stop": "end the machine those two started, and say what it cost",
    "studio": "open your SageMaker Studio space, or --stop it",
}

#: What each built verb does, in the sentence its own ``--help`` opens with.
#:
#: A SECOND TABLE BESIDE ``BUILT_TODAY`` BECAUSE THEY ANSWER TWO QUESTIONS. That one is one
#: line of the list :data:`BUILT_TODAY` holds, read by somebody choosing a verb, and it has to
#: fit beside every other line in it. This is the paragraph above the flags, read by somebody
#: who has already chosen and wants to know what they are about to run. ``edullm check
#: --help`` printed fifteen flags and never said what ``check`` was for, which made the
#: most-read page in the tool the one page that answered nothing.
#:
#: The unbuilt verbs are not here. Theirs is derived from :data:`NOT_BUILT_YET` in
#: :func:`build_parser_and_verbs`, so a plan cannot be described twice and differently, and
#: the retired names carry theirs in :data:`RETIRED` for the same reason.
WHAT_A_VERB_DOES: Final = {
    "check": (
        "Prices a submission from this working tree and lists every refusal, without "
        f"reaching a network. Writes a first {SPEC_PATH} where a registered repository has "
        "none."
    ),
    "submit": (
        f"Makes the same checks and then dispatches {SUBMIT_WORKFLOW}, waiting for the run "
        "id the compile job mints unless --no-wait says not to."
    ),
    "status": (
        "Names your recent submissions, or describes one run and asks AWS where the answer "
        "has moved past what GitHub can say. A run it cannot find is refused rather than "
        "asked about, unless --ask-aws says to."
    ),
    "logs": (
        "The last lines one run printed, read out of the report "
        f"{CANCEL_WORKFLOW} writes when asked to look at a run rather than stop it. A run "
        "it cannot find is refused rather than asked about, unless --ask-aws says to."
    ),
    "cancel": (
        "Stops one admitted run, with the reason you give. The run's history then records "
        "that reason instead of a failure."
    ),
    "add": (
        "Produces a change to the reviewed configuration rather than a grant to you. A "
        "repository is opened as a pull request from here, and the other kinds are refused "
        "with the route they go by."
    ),
    "ask": (
        "Files one issue on the platform repository, labelled with its kind and carrying "
        "which edullm and which reviewed configuration it was made from. It grants nothing. "
        "A person answers it."
    ),
    "run": (
        "Starts a machine of your own, copies this directory to it and runs the command "
        "after a bare --, streaming the output back. Nothing is checked against the "
        "registry and nothing is recorded as a run you can cite."
    ),
    "shell": (
        "A terminal on a machine of your own, or with --notebook a Jupyter you open in a "
        "browser on your laptop. The same machine edullm run uses for this project."
    ),
    "stop": (
        "Terminates the machine you have for this project and says what it ran up. Your "
        "files in the scratch bucket survive it; the machine's own disk does not. Nobody "
        "else's machine is reachable from here."
    ),
    "studio": (
        "Starts or resumes your own SageMaker Studio space and prints a sign-in URL, after "
        "saying what an hour of it costs. --stop ends the compute and keeps the disk. "
        "Nothing here is checked, priced against a policy or recorded as a run you can cite."
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
#: list of its own, so naming the replacement spelling there is the whole of what somebody
#: typing the old verb needs.
#: What ``docs-frank/reference/decisions.md`` settled on 2026-08-04 and nothing behind exists for
#: yet. Empty as of the exploration route, which is the point rather than an oversight: every verb
#: that document names is built. It stays because the next settled-and-unbuilt name has somewhere
#: to be declared, and because the answer to a name in it is a plan rather than a usage error.
#: :func:`_no_such_verb` reads it, and reads correctly when it is empty.
NOT_BUILT_YET: Final[dict[str, str]] = {}

#: The verbs that take a command for somebody else's program after a bare ``--``, and are
#: therefore the ones :func:`_split_at_the_dashes` runs on. Named rather than inferred, because
#: splitting a verb's arguments away from it is a thing to do deliberately: on any other verb it
#: would silently discard everything after a ``--`` argparse would have handled itself.
#:
#: Read a second time by :func:`_no_such_verb`, which keeps both out of the spellings it
#: offers for a word it does not know. They are the two that spend money ungated.
#:
#: **``stop`` IS A LANE VERB AND IS DELIBERATELY NOT IN THIS TUPLE**, which is worth saying
#: because its absence looks like an omission. This list is two things and ``stop`` is neither:
#: it takes no command for somebody else's program, so there is nothing to split at a ``--``,
#: and it spends nothing, so keeping it out of the spellings offered for an unknown word would
#: hide the one verb somebody reaching for "kill", "halt" or "terminate" is looking for.
LANE_VERBS: Final = ("run", "shell")

#: Words that are a request for the help rather than a guess at a verb. ``help`` is a verb in
#: git, cargo, docker and npm, so somebody typing it here has not misspelled anything and
#: should not be answered as though they had -- the answer they got was ``Did you mean
#: shell?``. Both are answered with what a bare ``edullm`` prints, which is the thing being
#: asked for.
#:
#: Not an alias and not a verb: nothing is added to :data:`BUILT_TODAY`, ``edullm help
#: check`` is not a thing, and the tenth name this design would have to carry is not created.
ASKING_FOR_THE_HELP: Final = frozenset({"help", "usage"})


def _split_at_the_dashes(tokens: Sequence[str]) -> tuple[list[str], tuple[str, ...]]:
    """This binary's arguments, and the researcher's program's, split at the first bare ``--``.

    Only the first, because a second one belongs to whatever is being run. No ``--`` at all
    means no command, which the verb answers with a sentence rather than by starting a machine
    for nothing.
    """
    tokens = list(tokens)
    if "--" not in tokens:
        return tokens, ()
    at = tokens.index("--")
    return tokens[:at], tuple(tokens[at + 1 :])


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
            "One machine and two clients, an editor over SSH or Jupyter, chosen at the "
            "point of asking rather than by picking a different verb."
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
            "install carries, which is the platform as it stood at the release you "
            "installed. Inside a platform checkout, --config-dir config reads the "
            "checkout's."
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
    _add_json(status)

    logs = verb_parser("logs", WHAT_A_VERB_DOES["logs"])
    logs.add_argument("run_id", help="the run to read")
    _add_ask_aws(logs)

    cancel = verb_parser("cancel", WHAT_A_VERB_DOES["cancel"])
    cancel.add_argument("run_id", help="the run to stop")
    cancel.add_argument(
        "--reason",
        required=True,
        help=(
            "why. Recorded on the termination, so the run's history says it was cancelled "
            "rather than that it broke."
        ),
    )

    add = verb_parser("add", WHAT_A_VERB_DOES["add"])
    # A POSITIONAL WITH `choices` RATHER THAN A FLAG, BECAUSE A MISTYPED KIND AND A KIND THAT
    # GOES THROUGH A PERSON ARE DIFFERENT FACTS. argparse answers the first with the list for
    # free; routing it to `ask` instead would file an issue asking a human for `add repositry`.
    add.add_argument("kind", choices=sorted(ADD_KINDS), help="what to teach the platform")
    add.add_argument(
        "--reason",
        help=(
            "why this needs a repository of its own rather than a workload in an existing "
            "one. Written into a comment above the entry, and required for a repository."
        ),
    )
    add.add_argument("--repository", help="overriding what the origin remote says")
    add.add_argument(
        "--dockerfile",
        default=f"{SPEC_DIRECTORY}/Dockerfile",
        help="repository-relative path to the Dockerfile the build workflow builds",
    )
    _add_json(add)

    ask = verb_parser("ask", WHAT_A_VERB_DOES["ask"])
    ask.add_argument("--kind", required=True, choices=sorted(ASK_KINDS), help="what kind of ask")
    ask.add_argument("--title", required=True, help="one line, as somebody scanning would read it")
    ask.add_argument("--detail", help="what you want, and what you have already tried")
    ask.add_argument("--run", help="a run this is about, where it is about one")
    _add_json(ask)

    run = verb_parser("run", WHAT_A_VERB_DOES["run"])
    _add_lane_arguments(run)
    run.add_argument(
        "command",
        nargs="*",
        help="the command to run, after a bare -- so its own flags reach it",
    )

    shell = verb_parser("shell", WHAT_A_VERB_DOES["shell"])
    _add_lane_arguments(shell)
    shell.add_argument(
        "--notebook",
        action="store_true",
        help="forward Jupyter to your laptop instead of opening a terminal",
    )

    stop = verb_parser("stop", WHAT_A_VERB_DOES["stop"])
    # NOT ``_add_lane_arguments``, AND THE THREE FLAGS IT WOULD ADD ARE THE ARGUMENT. --compute,
    # --hours and --spot all describe a machine about to be bought. This verb acts on one that
    # exists, whose shape, lifetime and market were settled when it started, so each of them
    # would be a flag a researcher could pass, watch be accepted, and have no effect -- which is
    # the shape of mistake ``_add_lane_arguments``' own note about --team argues against.
    #
    # AND NO ``--instance``, WHICH IS THE FLAG THIS VERB WILL NOT HAVE. It is the natural next
    # request and it is the one way past the fence: the machine acted on is whatever came back
    # from a describe filtered on the caller's own person tag, so an id typed here would be an
    # id that filter never saw. ``infra/iam/researcher-role.yaml`` does not fence this from
    # underneath -- see ``find_lane_machines_argv`` -- so the absence of the flag is the fence.
    stop.add_argument("--project", required=True, help="the machine for this project")

    studio = verb_parser("studio", WHAT_A_VERB_DOES["studio"])
    # ``--project`` IS NOT ``required=True`` HERE AND IT IS ON ``stop``, WHICH LOOKS LIKE AN
    # OVERSIGHT AND IS THE LANE'S OWN SPLIT. argparse's answer to a missing required flag is a
    # usage line and exit 2, which is right for a verb that can do nothing at all without one.
    # This verb can: ``--stop`` needs no project, because the space it acts on is the caller's
    # own and there is only ever one. So absence is judged by ``studio_refusals``, which answers
    # ``no_project`` with a code a skill can match on rather than a usage line it cannot.
    studio.add_argument("--project", help="what this space is for, which is what the tags carry")
    studio.add_argument(
        "--instance-type",
        help=(
            "a SageMaker shape other than the default, from the ones "
            f"{STUDIO_CONFIG_FILE} prices. Not an EC2 instance type: Studio has its own "
            "rate card and its own ml. names."
        ),
    )
    studio.add_argument(
        "--stop",
        action="store_true",
        help=(
            "end the compute and keep the disk. This is the one that matters -- nothing else "
            "stops an app, and the domain has no idle shutdown"
        ),
    )
    _add_json(studio)

    built: dict[str, argparse.ArgumentParser] = {
        "check": check,
        "submit": submit,
        "status": status,
        "logs": logs,
        "cancel": cancel,
        "add": add,
        "ask": ask,
        "run": run,
        "shell": shell,
        "stop": stop,
        "studio": studio,
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
            "ask AWS about a run this cannot find among your recent submissions. "
            f"Dispatches {CANCEL_WORKFLOW}, which spends a runner and waits for it. A run "
            "it does find is answered from GitHub either way."
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
            "outcome. The exit code is unchanged."
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

    # THE LANE VERBS ARE LISTED AND NEVER SUGGESTED, AND THE ASYMMETRY IS THE POINT. A
    # suggestion is read as an instruction, and `run` and `shell` are the two verbs that
    # start an instance without a price, an approval or a lineage record -- so offering one
    # to somebody who has just demonstrated they do not know the verbs puts the most
    # expensive thing in the tool one keystroke from the cheapest mistake in it. `edullm
    # help` was answered with "Did you mean shell?". They stay in the list below, where
    # choosing one is a choice rather than a prompt.
    known = sorted({*BUILT_TODAY, *NOT_BUILT_YET, *RETIRED} - set(LANE_VERBS))
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
            "refusal without reaching a network, and it writes a first "
            f"{SPEC_PATH} where a registered repository has none."
        ),
        "",
        "  edullm check --help    the flags one submission takes",
        "  edullm --help          this list, and the flags every verb takes",
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
                    "interrupted. Nothing was dispatched, so nothing is running that this started.",
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
                "and nothing here tried to. It is still running on GitHub, and edullm "
                "status names what it did.",
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
        "--wandb-project", help="the Weights and Biases project, defaulting to the team"
    )
    parser.add_argument("--repository", help="overriding what the origin remote says")
    parser.add_argument("--commit", help="overriding HEAD")
    parser.add_argument("--hours", help="override the workload's runtime bound")
    parser.add_argument("--attempts", type=int, help="override the workload's attempt bound")
    parser.add_argument("--fanout-size", type=int, help="cells in a fan-out")
    parser.add_argument("--fanout-index-parameter", help="what the fan-out index varies")
    parser.add_argument("--spec", type=Path, help=f"a spec other than the {SPEC_PATH} above you")


def _add_lane_arguments(parser: argparse.ArgumentParser) -> None:
    """The three things a lane ask needs, and the one that changes how the machine is bought.

    Deliberately not :func:`_add_submission_arguments`. A submission names a dataset, an
    experiment, a workload profile and a W&B project because a record names them; a lane ask
    names a machine and a place to put files. Sharing the flag set would be sharing the meaning.

    **``--project`` IS REQUIRED AND ``--compute`` STOPPED BEING SO ON 2026-08-06, AND THE
    DIFFERENCE IS NOT INCONSISTENCY.** A project is a name only the person has: it tags the
    instance and the volume, it is the last segment of the working prefix, and two unrelated
    pieces of work under one name is two unrelated pieces of work on one bill that nothing
    afterwards can separate. A machine is a price, it is declared in reviewed configuration,
    and :func:`~edullm_platform.cli.lane.default_compute_profile` picks the cheapest shape whose
    card can run what a trainer defaults to -- the argument ``cli/scaffold.py`` already makes,
    over the same catalog. It is announced with its rate before anything starts.

    **NO ``--team``, WHICH THIS CARRIED UNTIL 2026-08-05.** It set the first segment of the
    working prefix and nothing else, and the prefix is ``<person>/<project>/`` now. A flag that
    stayed on for compatibility would be one a researcher could type, watch be accepted, and
    then not find in the path, which is worse than the error argparse gives for a flag that is
    gone. ``edullm submit`` keeps its own ``--team``: a run is charged to a group.

    **NO ``--json`` HERE, WHICH IS A DECISION RATHER THAN AN OMISSION.**
    ``cli/machine.py``'s own header gives the rule: a document is published where a structure
    already exists and is invented nowhere else. ``run``'s stdout is the researcher's program's
    output, streamed, and ``shell``'s is a terminal handed to a child. Neither can carry one
    document, and a flag that emptied stdout of the one thing the verb is for would be worse
    than having none. ``tests/test_cli_shell.py`` holds this.
    """
    parser.add_argument("--project", required=True, help="what this machine is for")
    parser.add_argument("--compute", help="the machine, from the catalog; one is picked if absent")
    parser.add_argument(
        "--hours",
        type=_whole_hours,
        help="whole hours before the machine may be stopped. The lane sets the default",
    )
    parser.add_argument(
        "--spot",
        action="store_true",
        help="buy it on Spot, as a persistent request that stops rather than terminates",
    )


def _whole_hours(text: str) -> int:
    """``--hours`` on a lane verb, which is a whole number and not the submission's decimal.

    ITS OWN TYPE SO THAT BOTH SPELLINGS OF THE MISTAKE GET ONE ANSWER. ``--hours`` on ``check``
    is parsed by hand into a ``Decimal``, because a bound that went through binary floating
    point is not the number the approver reads, and :func:`_unreadable_hours` answers a bad one
    with a sentence that says fractions are fine. On a lane verb they are not: the lifetime tag
    is whole hours. Left to a bare ``type=int``, ``--hours 0.5`` would get argparse's line and
    ``--hours 0`` would get that other sentence recommending exactly what argparse had just
    refused. Both are argparse's here, so both read the same.
    """
    try:
        hours = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a whole number. A lane machine's lifetime is tagged in whole "
            "hours, so 4 works and 4.5 does not."
        ) from None
    if hours < 1:
        raise argparse.ArgumentTypeError(
            f"{hours} is not a lifetime. A machine has to live at least an hour to be worth "
            "starting, and the janitor stops it at the end of the one you ask for."
        )
    return hours


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
    if not tokens or word in ASKING_FOR_THE_HELP:
        print(_orientation(), end="", file=stderr)
        return EXIT_UNUSABLE
    if word is not None and word not in BUILT_TODAY and word not in NOT_BUILT_YET:
        print(_no_such_verb(word), end="", file=stderr)
        return EXIT_UNUSABLE

    # BEFORE ARGPARSE RATHER THAN THROUGH ``nargs=REMAINDER``, WHICH GETS THIS BACKWARDS.
    # REMAINDER starts collecting at the first token it does not recognise, so
    # `edullm run --project p --compute c --nonesuch x` is not a usage error at all: the typo
    # and its value become the researcher's command, a machine starts, and `--nonesuch x` runs
    # on it. Splitting here leaves argparse a head it can be strict about, which is what makes
    # a misspelled flag on a lane verb the same exit 2 it is on the other seven.
    tokens, after_the_dashes = _split_at_the_dashes(tokens) if word in LANE_VERBS else (tokens, ())

    parser, verb_parsers = build_parser_and_verbs()
    arguments, unknown = parser.parse_known_args(tokens)
    verb = arguments.verb
    if verb in NOT_BUILT_YET:
        print(
            f"{verb} is not built yet. It is settled and it would "
            f"{NOT_BUILT_YET[verb]}, and nothing behind it exists. Built today: "
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
        if verb == "add":
            return _add(
                arguments,
                runner=command_runner,
                out=stdout,
                err=stderr,
                cwd=here,
                dispatched=dispatched,
            )
        if verb == "ask":
            return _ask(arguments, runner=command_runner, out=stdout, err=stderr)
        if verb == "run":
            arguments.command = list(after_the_dashes)
            return _run(arguments, runner=command_runner, out=stdout, err=stderr, cwd=here)
        if verb == "shell":
            return _shell(
                arguments,
                after_the_dashes,
                runner=command_runner,
                out=stdout,
                err=stderr,
                cwd=here,
            )
        if verb == "stop":
            return _stop(arguments, runner=command_runner, out=stdout, err=stderr)
        if verb == "studio":
            return _studio(arguments, runner=command_runner, out=stdout, err=stderr)
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
        #
        # THE STREAM IS THE ONE THING THAT DOES DEPEND ON THE VERB. A caller that asked for
        # a document has to get one on stdout, and this is the only refusal in the binary
        # that arrives from outside the verb answering for it. ``getattr`` because three of
        # the verbs that resolve an id do not carry the flag at all.
        if getattr(arguments, "json", False):
            emit(refusal_document(verb, [_ambiguous_run_id(exc)]), out=stdout)
        else:
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
    facts = read_git_facts(runner, cwd=cwd, submitting=arguments.commit)
    submitter = github_login(runner, allow_network=False)
    in_hand = _spec_for_checking(arguments, configuration, facts, cwd=cwd)
    if in_hand.written is not None:
        # READ THE TREE AGAIN, BECAUSE THIS INVOCATION JUST CHANGED IT.
        #
        # The facts above were read before the scaffold was written, so on the one run that
        # writes a file they describe a tree that no longer exists. Everything below has to
        # reason about the tree as it is: `working_tree_refusals` excludes a spec nobody has
        # ever committed by asking git what it says about that path, and it can only do that
        # if git has been asked since the file appeared.
        #
        # Re-reading rather than adjusting the facts in place, because the same function
        # answering twice cannot disagree with itself the way a patched copy could. It
        # costs five git calls on the one invocation in a repository's life that writes a
        # spec, and nothing on any other.
        facts = read_git_facts(runner, cwd=cwd, submitting=arguments.commit)
        # ON STDERR UNDER --json, BECAUSE STDOUT IS ONE DOCUMENT THERE AND NOTHING ELSE.
        # A caller pipes stdout into a parser, and the one invocation in a repository's life
        # that writes a spec is exactly the one a first-run agent makes.
        wrote = err if arguments.json else out
        print(_scaffolded_said(in_hand.written), file=wrote)
        print(file=wrote)
    preflight = _preflight(
        arguments,
        configuration,
        facts,
        in_hand.spec,
        submitter,
        unscaffoldable=in_hand.unscaffoldable,
        spec_path=in_hand.path,
    )
    # SAID HERE TOO, BECAUSE THE FREE VERB WAS THE ONE VERB THAT WOULD NOT SAY IT.
    #
    # `run` and `shell` have carried this since the lane was built. `check` did not, so the
    # command whose whole job is to tell somebody whether a submission will work was silent
    # about the one reason it will not, while the two ungated verbs that spend money without
    # an approval both spoke up. Measured on 2026-08-06: `check --compute gpu-8xl40s` priced
    # a run at $1,446.30 and routed it to a lead, against a file recording 4,060 refusals for
    # that shape over two days and not one instance obtained.
    #
    # Above the rest rather than beside the price. It is a fact about whether any of the
    # numbers below will ever be spent, and a reader who has reached the cost block has
    # already decided.
    verdict = placement_verdict(configuration, preflight.request.compute_profile)
    # NOT SAID WHERE THE SHAPE HAS NO COMPUTE ENVIRONMENT, BECAUSE THE TWO CONTRADICT.
    #
    # The placement sentence ends "and nothing here refuses it". That is true of the lane
    # verbs, which refuse nothing, and true of a provisioned shape that places badly. On
    # `gpu-8xh100` it landed two lines above `unprovisioned_compute_profile`, so this verb
    # said nothing refuses it and then refused it, in one screen -- and a reader who believes
    # the first sentence reads the refusal as a bug.
    #
    # The refusal is the stronger fact and the actionable one. "A machine may take a while to
    # arrive, or never arrive" describes a queue that exists; there is no compute environment
    # for p5.48xlarge at all, and the refusal says so and lists what is provisioned instead.
    # The --json document keeps both, because two keys a caller reads separately cannot
    # contradict each other the way two sentences in one screen do.
    said = (
        None
        if any(refusal.code == "unprovisioned_compute_profile" for refusal in preflight.refusals)
        else placement_said(verdict)
    )
    if said is not None:
        print("\n".join(_wrapped(said, indent="")), file=err if arguments.json else out)
        print(file=err if arguments.json else out)
    if arguments.json:
        emit(
            check_document(
                preflight,
                configuration=configuration,
                submitter=submitter,
                placement=verdict,
            ),
            out=out,
        )
    else:
        print(render_preflight(preflight, configuration=configuration), end="", file=out)
    return EXIT_REFUSED if preflight.refused else EXIT_OK


# ---------------------------------------------------------------------------------------
# the lane, which run and shell share
# ---------------------------------------------------------------------------------------

#: How often the agent is asked whether it has registered. Five seconds against a wait measured
#: in minutes, which is often enough that the answer arrives promptly and rare enough that the
#: waiting costs a handful of calls rather than hundreds.
AGENT_POLL_SECONDS: Final = 5.0


@dataclass(frozen=True)
class _LaneSession:
    """A lane entered, a machine found or started, and the credential the rest of it uses."""

    request: LaneRequest
    machine: str
    #: When the janitor may stop this machine, and the line that says so, as one value. It was
    #: a bare string computed against this invocation's clock, which is true of a machine being
    #: started and false of one being found: a reused machine kept the tag its launch wrote and
    #: was described with a later instant it would not be honoured to.
    expiry: LaneExpiry
    environment: dict[str, str]
    #: The working tier's four numbers, read once for the whole session. ``edullm shell``
    #: read them a second time for the notebook port, out of a second resolution of a
    #: directory that had already been resolved, which is the arrangement that lets one
    #: invocation answer out of two installs.
    settings: WorkingTierSettings


def _lane_session(
    arguments: argparse.Namespace,
    configuration: ReviewedConfiguration,
    *,
    runner: CommandRunner,
    err: TextIO,
) -> _LaneSession | int:
    """Everything both lane verbs do before they differ, or the exit code that stopped them.

    An exit code rather than an exception, because the three ways this stops are three different
    codes and a caller that collapsed them would be the thing this binary's exit-code table exists
    to prevent.

    **NOTHING HERE CALLS ``run_preflight``.** ``tests/test_lane_verdicts.py`` fails if it ever
    does, and the argument is in ``cli/lane.py``'s header: the refusals that path makes are about
    a submission that will be recorded, approved and cited, and none of those three happens here.

    **THE TWO SETTINGS FILES ARE READ FIRST, BEFORE THE PLUGIN CHECK AND BEFORE ANY CALL.**
    They were read after ``sts:GetCallerIdentity`` and after the refusals, which meant a broken
    installation was reported only to somebody who already held a session, and the one thing this
    module could answer with no account at all was the last thing it did. Both reads are pure
    local file reads off ``configuration.directory``, so putting them first costs nothing and
    makes an installation that cannot find its own numbers say so immediately.

    **THREE LOCAL WALLS BEFORE THE FIRST CALL, IN THE ORDER A NEWCOMER FAILS THEM.** The broker
    binary, then the session plugin, then a profile to run under. The broker is first because it
    precedes the other two rather than sitting beside them: the AWS session the third one selects
    is minted by the first, so a laptop without it fails every later step for a reason none of
    them names. Until 2026-08-06 nothing checked for it at all and the fifteen people who have
    never been able to get it fell through to a shell "command not found" out of
    ``credential_process`` -- not a refusal, no code, nothing to search for, and no route to the
    queue that could actually help them.
    """
    settings = load_working_tier_settings(configuration.directory)
    hours = arguments.hours or load_lane_settings(configuration.directory).default_lifetime_hours
    if shutil.which(AWS_BROKER) is None:
        print(render_refusals((missing_broker_refusal(),)), end="", file=err)
        return EXIT_UNUSABLE
    if shutil.which(SESSION_PLUGIN) is None:
        print(_missing_plugin_said(), end="", file=err)
        return EXIT_UNUSABLE
    # THE PROFILE IS RESOLVED HERE AND HANDED TO THE TWO CALLS BELOW RATHER THAN EXPORTED. Both
    # take the ambient environment overlaid with what they are given, so a profile in `env` is
    # the same thing to `aws` as one in the shell -- and this process does not mutate an
    # environment it shares with whatever started it. Past `assume-role` there is nothing to
    # carry: `credentials_environment` puts the lane's own keys in `env` for every later call,
    # and AWS_PROFILE beside them would be a second answer to a question already answered.
    aws_config = aws_config_path(os.environ, home=Path.home())
    resolved = resolve_aws_profile(
        read_aws_config(aws_config),
        declared=os.environ.get(AWS_PROFILE_VARIABLE),
        path=aws_config,
    )
    if resolved.refusal is not None:
        print(render_refusals((resolved.refusal,)), end="", file=err)
        return EXIT_UNUSABLE
    if resolved.said is not None:
        print(resolved.said, file=err)
    # ``None`` and not an empty mapping where nothing was chosen, so that a person who set
    # AWS_PROFILE themselves gets the call this made before any of this existed.
    profile = None if resolved.profile is None else {AWS_PROFILE_VARIABLE: resolved.profile}
    identity = runner(("aws", "sts", "get-caller-identity", "--output", "json"), env=profile)
    if not identity.ok:
        print(_no_aws_session(identity.stderr, opens_a_session=True), end="", file=err)
        return EXIT_UNREACHABLE
    facts = json.loads(identity.stdout)
    person = person_from_caller_arn(str(facts["Arn"])) or ""
    # RESOLVED HERE BECAUSE THE REFUSALS AND THE PLACEMENT WARNING BELOW ARE ABOUT A SHAPE, AND
    # PRINTED LATER BECAUSE A SHAPE THIS CHOSE IS ONLY NEWS WHERE A MACHINE IS ABOUT TO START.
    # It was announced here for its first hours, above the branch that finds an existing machine,
    # so a second invocation read "this starts gpu-1xl4: g6.xlarge at $0.8048/hour" and then
    # "found that machine rather than starting one" -- two sentences that cannot both be true,
    # and the rate belonged to a shape the person was not being given, because reuse does not
    # check that the machine it found is the shape anybody asked for.
    defaulted = None if arguments.compute else default_compute_profile(configuration)
    request = LaneRequest(
        project=arguments.project or "",
        person=person,
        compute_profile=arguments.compute or (defaulted.profile if defaulted else ""),
    )
    # NO ``resolve_team`` CALL, AND ITS REMOVAL ON 2026-08-05 IS THE POINT RATHER THAN A
    # SIMPLIFICATION. The working tier is laid out ``<person>/<project>/`` now, so there is no
    # group anywhere in what this verb decides. Resolving one anyway would put
    # ``team_is_ambiguous`` in front of the seven people who sit on two groups and the owner who
    # sits on two, every time they asked for a machine, to settle a segment that is not in the
    # path. The submission path still resolves a team, because a run is charged to one.
    refusals = lane_refusals(request, configuration=configuration)
    if refusals:
        print(render_refusals(refusals), end="", file=err)
        return EXIT_REFUSED

    warning = placement_warning(configuration, request.compute_profile)
    if warning is not None:
        print("\n".join(_wrapped(warning, indent="")), file=err)

    # THE SAME PROFILE THIS RESOLVED, BECAUSE THIS IS THE LAST CALL MADE AS THE PERSON. It is
    # the one that turns their credential into the lane's, and a resolution that reached
    # GetCallerIdentity and not this would prove an identity and then assume from a different
    # one -- or from none, which is the failure it exists to remove.
    assumed = runner(
        assume_lane_argv(
            account=str(facts["Account"]),
            project=request.project,
            person=request.person,
            lifetime_hours=hours,
        ),
        env=profile,
    )
    if not assumed.ok:
        print(_cannot_enter_the_lane(assumed.stderr), end="", file=err)
        return EXIT_UNREACHABLE
    environment = credentials_environment(json.loads(assumed.stdout)["Credentials"])

    found = runner(
        find_machine_argv(project=request.project, person=request.person), env=environment
    )
    # THE EXPIRY OF A MACHINE THAT ALREADY EXISTS IS READ OFF IT AND NOT COMPUTED HERE. It was
    # computed, once, above this branch and for both of them, so a second invocation printed an
    # instant the ExpiresAt tag did not carry and the janitor was never going to honour.
    reused = machine_already_running(found.stdout)
    if reused is not None:
        machine, carried = reused
        return _LaneSession(
            request=request,
            machine=machine,
            expiry=carried,
            environment=environment,
            settings=settings,
        )

    # STILL BEFORE ANYTHING IS BOUGHT, WHICH IS THE PROPERTY THAT MAKES THE DEFAULT DEFENSIBLE
    # RATHER THAN A SURPRISE: the objection to answering this flag is that it spends money nobody
    # named, and this is the last line before the call that spends it.
    if defaulted is not None:
        print("\n".join(_wrapped(defaulted.said, indent="")), file=err)

    expiry = expiry_for_a_new_machine(datetime.now(tz=UTC), hours)
    started = _start_a_machine(
        request,
        configuration=configuration,
        settings=settings,
        expiry=expiry.value,
        spot=bool(arguments.spot),
        # WHETHER THE PERSON CHOSE THIS SHAPE, WHICH ONLY THE ALL-ZONES-OUT REFUSAL READS. A
        # defaulted shape that cannot start is worse than a typed one: the researcher did not
        # pick it, so "pass --compute for a different one" is advice they cannot act on until
        # somebody tells them they never passed it in the first place.
        defaulted=defaulted is not None,
        runner=runner,
        environment=environment,
        err=err,
    )
    if isinstance(started, int):
        return started
    return _LaneSession(
        request=request,
        machine=started,
        expiry=expiry,
        environment=environment,
        settings=settings,
    )


def _start_a_machine(
    request: LaneRequest,
    *,
    configuration: ReviewedConfiguration,
    settings: WorkingTierSettings,
    expiry: str,
    spot: bool,
    defaulted: bool,
    runner: CommandRunner,
    environment: dict[str, str],
    err: TextIO,
) -> str | int:
    """Launch one in whichever zone will have it, and wait until Systems Manager answers.

    The image, the subnets and the security group are all resolved rather than pinned. An AMI id
    ages out, a subnet id is a fact about a stack somebody may redeploy, and a hard-coded security
    group is the one that stops having zero ingress rules without anybody noticing.

    **IT TAKES THE FIRST ZONE THAT WILL SELL ONE RATHER THAN THE FIRST SUBNET EC2 LISTS, WHICH
    IS WHAT THIS FUNCTION SHIPPED DOING.** ``describe-subnets`` came back as a bare list of ids
    and this took ``[0]`` of it, which for this account is ``us-east-1f`` every time. At 09:52
    UTC on 2026-08-06 that zone had no ``g6.xlarge`` and three attempts in a row were refused by
    it, because there was no way for a second attempt to be anywhere else. ``g6.xlarge`` is the
    shape ``default_compute_profile`` picks when nobody passes ``--compute``, so one zone
    running short is every first command failing at once.

    **THE LOOP STOPS ON ANYTHING IT DOES NOT RECOGNISE, AND THAT IS THE EXPENSIVE HALF TO GET
    WRONG.** ``another_zone_may_answer`` is what decides, and :data:`~edullm_platform.cli.lane.
    ZONE_SHAPED_REFUSALS` carries the argument: a refusal that allocated nothing may be asked
    again somewhere else, and a call whose outcome could not be read may not, because a second
    ``RunInstances`` after an unreadable first is how one command leaves two machines billing.
    An authorization denial and a vCPU quota both stop here on the first zone and are reported
    exactly as they always were.
    """
    image = runner(
        (
            "aws",
            "ssm",
            "get-parameter",
            "--name",
            GPU_AMI_PARAMETER,
            "--query",
            "Parameter.Value",
            "--output",
            "text",
        ),
        env=environment,
    )
    subnets = runner(find_subnets_argv(), env=environment)
    groups = runner(
        (
            "aws",
            "ec2",
            "describe-security-groups",
            "--filters",
            f"Name=group-name,Values={PLATFORM_NETWORK_NAME}",
            "--query",
            "SecurityGroups[].GroupId",
            "--output",
            "json",
        ),
        env=environment,
    )
    declared = lane_subnets(subnets.stdout)
    group_ids = json.loads(groups.stdout or "[]")
    if not image.ok or not declared or not group_ids:
        print(_no_network_for_the_lane(), end="", file=err)
        return EXIT_UNREACHABLE

    instance_type = instance_type_for(configuration, request.compute_profile)
    # Already refused where it is None, and mypy has no way to know that.
    assert instance_type is not None
    # ASKED AFTER THE SUBNETS AND BEFORE THE LAUNCH, BECAUSE IT NARROWS A LIST RATHER THAN
    # GATING ONE. The us-east-1e subnet is declared for p5 and nothing else, so a g6 sent there
    # is one refusal spent on a zone that can never answer; a call that fails here leaves every
    # subnet a candidate, which costs at most that one refusal back.
    offerings = runner(zones_offering_argv(instance_type), env=environment)
    candidates = subnets_to_try(
        declared, offered_in=zones_offering(offerings.stdout) if offerings.ok else frozenset()
    )

    attempts: list[ZoneAttempt] = []
    # None rather than "" for the found machine, because an empty id is a launch that answered
    # nothing and is not the same thing as every zone having refused -- collapsing them would
    # print a refusal naming no zones at all.
    machine: str | None = None
    for candidate in candidates:
        launched = runner(
            run_instances_argv(
                request=request,
                instance_type=instance_type,
                image_id=image.text,
                subnet_id=candidate.subnet,
                security_group_id=group_ids[0],
                expires_at_value=expiry,
                settings=settings,
                spot=spot,
            ),
            env=environment,
        )
        if launched.ok:
            machine = launched.text
            break
        if not another_zone_may_answer(launched.stderr):
            print(_launch_refused(launched.stderr), end="", file=err)
            return EXIT_UNREACHABLE
        attempts.append(ZoneAttempt(zone=candidate.zone, said=launched.stderr))
        print(f"no {instance_type} in {candidate.zone}, trying another zone", file=err)
    if machine is None:
        # ``attempts`` cannot be empty here: ``declared`` was checked above and
        # ``subnets_to_try`` falls through rather than emptying the list, so every candidate
        # either produced a machine or produced a refusal that was recorded.
        print(
            _no_zone_had_it(
                instance_type=instance_type,
                profile=request.compute_profile,
                attempts=attempts,
                defaulted=defaulted,
            ),
            end="",
            file=err,
        )
        return EXIT_UNREACHABLE
    print(f"starting {machine}, waiting for it to answer", file=err)
    deadline = time.monotonic() + settings.boot_wait_seconds
    while time.monotonic() < deadline:
        ping = runner(agent_online_argv(machine), env=environment)
        if ping.text == "Online":
            return machine
        time.sleep(AGENT_POLL_SECONDS)
    print(_machine_never_answered(machine, settings), end="", file=err)
    return EXIT_UNREACHABLE


def _missing_plugin_said() -> str:
    """The plugin refusal and, under it, the commands AWS documents for this laptop.

    **THE THREE FACTS ARE MEASURED HERE AND THE MESSAGE IS DECIDED IN ``cli/lane.py``, WHICH
    IS THE SEAM ``gh_config_directory`` ALREADY SITS ON.** That module runs no process and
    reads no environment, so it cannot ask which operating system this is; what it can do is
    pick the right one of AWS's five installers once somebody tells it, which is what makes
    the Windows text assertable from a Mac. ``dpkg`` decides only which Linux package family
    to name and is not consulted about the other two platforms.

    **THE COMMANDS ARE PRINTED UNDER THE BLOCK RATHER THAN INSIDE IT, AND THAT IS THE WHOLE
    REASON THIS FUNCTION EXISTS.** ``render_refusals`` wraps a detail at 76 columns, so a
    ``curl "..." -o "..."`` carried in one arrives split across four indented lines and has
    to be reassembled before it runs -- which is most of the work this message was rewritten
    to remove. Printed here they are one line each, exactly as AWS gives them.
    """
    refusal = missing_plugin_refusal(
        system=platform.system(),
        machine=platform.machine(),
        has_dpkg=shutil.which("dpkg") is not None,
    )
    commands = plugin_install_commands(
        system=platform.system(),
        machine=platform.machine(),
        has_dpkg=shutil.which("dpkg") is not None,
    )
    return "".join(
        [
            render_refusals((refusal,)),
            "\n",
            *(f"  {command}\n" for command in commands),
            "\n",
        ]
    )


def _no_aws_session(said: str, *, opens_a_session: bool) -> str:
    """No credential at all, which is the first thing a newcomer hits and is not a refusal.

    **IT NAMES THE COMMAND, BECAUSE THERE IS EXACTLY ONE AND ASSUMING IT IS KNOWN IS HOW A
    NEWCOMER GETS STUCK HERE.** This used to say "log in the way you normally do", which is
    an instruction for somebody who has already done it once. Every human session in this
    account comes from the broker and from nothing else -- there are no long-lived keys to
    fall back on and creating one is refused -- so the way is ``sb-aws-creds login``, and a
    refusal that will not say so is asking the reader to go and find out.

    **AND THEN IT SAYS WHAT TO DO WHEN THAT COMMAND IS NOT A COMMAND, WHICH IS THE CASE FOR
    THE FIFTEEN PEOPLE MOST LIKELY TO READ THIS.** ``sb-aws-creds`` is a private package
    published out of another repository: ``guides/the-platform.md`` records ``npm view
    sb-aws-creds`` answering 404 on 2026-08-06, so there is no public install line and there
    is not going to be one. Naming the command and stopping there sends everybody who does
    not already have the broker into a shell error, which reads as a broken refusal rather
    than as a missing prerequisite, and gives them nothing to do next. The route the guide
    settled on is named here instead, at the moment somebody needs it.

    **``opens_a_session`` IS WHAT KEEPS THE PREREQUISITE COUNT TRUE FOR EACH CALLER, AND IT
    IS NOT DECORATION.** :func:`_lane_session` checks the Session Manager plugin before it
    makes this call, so a reader who reached this from ``run`` or ``shell`` has already got
    past that gate and needs to be told there is no third wall behind this one -- a person
    who has just been sent to install something assumes there is. :func:`_stop` opens no
    session, checks no plugin and is deliberately usable on a laptop whose plugin has broken,
    so the same sentence there would invent a prerequisite the verb does not have. One
    paragraph asserting a fact about a gate the caller never ran is the shape of defect this
    whole message is being repaired for.
    """
    prerequisites = (
        "That is the second and last of the two things these verbs want on your laptop, and "
        "the Session Manager plugin is the first, which this already found on your PATH."
        if opens_a_session
        else "That is the only thing this verb wants on your laptop. It opens no session on "
        "the machine, so the Session Manager plugin the other lane verbs need is not a "
        "prerequisite here."
    )
    return "\n".join(
        [
            "",
            *_wrapped(
                "AWS would not say who you are, so no machine was asked for. The lane needs an "
                f"AWS session the way the recorded path needs gh: run `{AWS_LOGIN_COMMAND}`, "
                f"complete the browser approval it opens, and run this again. {prerequisites} "
                f"If your shell has no `{AWS_LOGIN_COMMAND.split()[0]}` at all, that broker is "
                "a private package with no public install line, and `edullm ask --kind "
                "access-request` is the route to it. "
                f"What AWS said: {said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _cannot_enter_the_lane(said: str) -> str:
    return "\n".join(
        [
            "",
            *_wrapped(
                "the researcher role would not let this session in, so nothing was started. The "
                "role is what gives you a machine without an administrator, and entering it "
                f"needs a session it trusts. What AWS said: {said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _no_network_for_the_lane() -> str:
    return "\n".join(
        [
            "",
            *_wrapped(
                "the platform's own network could not be found, so there is nowhere to put a "
                "machine. That is a deploy having not happened rather than anything about what "
                "you asked for, and nothing is billing.",
                indent="",
            ),
            "",
        ]
    )


def _launch_refused(said: str) -> str:
    """What EC2 said, verbatim, for every refusal a second zone could not answer.

    Still the right message for those. An authorization denial, a malformed parameter and a
    vCPU quota are all the same in every zone, so quoting AWS once and stopping is both the
    fastest answer and the only honest one -- the loop above is what makes sure this is not
    also reached for the one refusal that is about a zone rather than about the request.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                "EC2 would not start the machine, and nothing is billing. What it said: "
                f"{said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _no_zone_had_it(
    *, instance_type: str, profile: str, attempts: Sequence[ZoneAttempt], defaulted: bool
) -> str:
    """Every zone refused, wrapped for a terminal.

    The sentence is ``cli/lane.py``'s, for the reason that module composes every other line
    that quotes a decision it made: what was tried, in what order, and whether the shape was
    chosen by the person are all facts the launch holds and this file would have to be handed.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                no_zone_had_this_shape(
                    instance_type=instance_type,
                    profile=profile,
                    attempts=attempts,
                    defaulted=defaulted,
                ),
                indent="",
            ),
            "",
        ]
    )


def _machine_never_answered(machine: str, settings: WorkingTierSettings) -> str:
    """**THE ONE MESSAGE HERE THAT HAS TO NAME A MACHINE, BECAUSE ONE IS RUNNING.**

    Every other failure above leaves nothing behind. This one has an instance that started and did
    not register, so it is billing, and a message that did not name it would leave somebody with a
    charge and no id to look it up by. The expiry still holds, which is the sentence that turns a
    frightening message into a bounded one.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                f"{machine} started and has not answered Systems Manager within "
                f"{settings.boot_wait_seconds} seconds. It is running and it is billing. Its "
                "expiry tag still holds, so it will be stopped on schedule whatever happens "
                "next, and edullm run against the same project reaches it if it comes up.",
                indent="",
            ),
            "",
        ]
    )


# ---------------------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------------------

#: What a POSIX shell exits with when it cannot find the command it was given. Not this
#: binary's own exit code and never returned by it: it is read off the remote sentinel, where
#: it is the status of the researcher's own line on the machine.
COMMAND_NOT_FOUND: Final = 127


def _run(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    cwd: Path,
) -> int:
    """A machine, this directory on it, and one command's output coming back.

    **NOTHING HERE CALLS ``run_preflight`` AND THAT IS THE POINT OF THE VERB.**
    ``tests/test_lane_verdicts.py`` fails if it ever does. The refusals that path makes are
    about a submission that will be recorded, approved and cited, and none of those three happens
    here.
    """
    configuration = _configuration(arguments)
    if not arguments.command:
        print(_run_needs_a_command(), end="", file=err)
        return EXIT_UNUSABLE
    session = _lane_session(arguments, configuration, runner=runner, err=err)
    if isinstance(session, int):
        return session

    uri = working_uri(person=session.request.person, project=session.request.project)
    print("\n".join(_wrapped(session.expiry.said(session.machine), indent="")), file=out)
    print(
        "\n".join(
            _wrapped(
                f"Your files go to {uri} and land in "
                f"{work_directory(session.request.project)} on the machine, which is where "
                "the command runs. Nothing here is recorded as citable, and nothing was "
                "checked.",
                indent="",
            )
        ),
        file=out,
    )
    print("\n".join(_wrapped(what_the_machine_carries(), indent="")), file=out)
    print(file=out)

    command = command_line(arguments.command)
    runner(
        ("aws", "s3", "sync", str(cwd), uri, "--exclude", ".git/*", "--only-show-errors"),
        env=session.environment,
    )
    streamed = runner(
        remote_command_argv(
            session.machine,
            command=remote_script(uri=uri, project=session.request.project, command=command),
        ),
        env=session.environment,
        # THE SESSION READS NO KEYSTROKES AND MUST NOT DIE FOR WANT OF SOMEWHERE TO READ THEM.
        # `SubprocessRunner._a_stdin_nobody_closes` carries what the plugin does with end of
        # file on descriptor 0 and what it cost. `edullm shell` deliberately does not ask for
        # this, because there the researcher is typing.
        stdin_stays_open=True,
    )
    print(_without_the_sentinel(streamed.stdout), end="", file=out)
    status = _remote_status(streamed.stdout)
    if status is None:
        print(
            "\n".join(
                _wrapped(
                    "the session ended without reporting what the command did, so nothing here "
                    "judged it. The machine may have been interrupted. edullm run again reaches "
                    "the same machine while it lives.",
                    indent="",
                )
            ),
            file=err,
        )
        return EXIT_UNREACHABLE
    if status != 0:
        print(f"the command exited {status}", file=err)
        # ONE STATUS GETS A SENTENCE AND THE REST GET THE NUMBER, BECAUSE ONE OF THEM IS ABOUT
        # THE MACHINE AND THE REST ARE ABOUT THE RESEARCHER'S PROGRAM. 127 is the shell saying
        # it could not find the command at all, which is a fact about what is installed here,
        # and it is what the first `edullm run` anybody ever made came back with.
        if status == COMMAND_NOT_FOUND:
            print("\n".join(_wrapped(command_not_found_said(), indent="")), file=err)
        return EXIT_REFUSED
    return EXIT_OK


def _without_the_sentinel(streamed: str) -> str:
    """The remote output as the researcher's program wrote it, minus the wrapper's last line.

    The sentinel is this binary talking to itself. Printing it would put ``edullm-exit:0`` at the
    bottom of every run's output, which is a thing somebody would eventually grep for, and then
    it would be a format rather than an implementation detail.
    """
    lines = streamed.splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith("edullm-exit:")]
    return "".join(kept)


def _remote_status(streamed: str) -> int | None:
    """The remote command's exit status, read off the sentinel the wrapper printed.

    ``start-session`` exits with the plugin's status rather than the remote command's, so this is
    the only place the verb can learn what happened. Absent means the session ended before the
    wrapper reached its last line, which is a different fact from a non-zero status and gets a
    different exit code.
    """
    for line in reversed(streamed.splitlines()):
        if line.startswith("edullm-exit:"):
            try:
                return int(line.removeprefix("edullm-exit:").strip())
            except ValueError:
                return None
    return None


def _run_needs_a_command() -> str:
    """Why a machine was not started, for somebody who typed the verb and stopped."""
    return "\n".join(
        [
            "",
            *_wrapped(
                "run takes the command after a bare --, so its own flags reach it rather than "
                "this one. Starting a machine with nothing to run would leave it billing until "
                "its expiry, and edullm shell is the verb for a machine to sit at.",
                indent="",
            ),
            "",
            "  edullm run --project mixlaw --compute gpu-1xt4 -- python train.py",
            "",
        ]
    )


# ---------------------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------------------

#: Where a forwarded notebook appears on the laptop. Different from the machine's own port so
#: that a Jupyter the researcher is already running locally is not what they end up looking at,
#: which is a confusion that costs half an hour and looks exactly like a working notebook.
LOCAL_NOTEBOOK_PORT: Final = 8890


def _shell(
    arguments: argparse.Namespace,
    after_the_dashes: Sequence[str],
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    cwd: Path,
) -> int:
    """A terminal on a machine of your own, or a notebook forwarded to your browser.

    **NOTHING HERE CALLS ``run_preflight`` EITHER.** ``tests/test_lane_verdicts.py`` holds both
    verbs to that, and the reason is the same one: nothing is recorded, approved or cited.

    **IT SHIPS THE TREE AND CARRIES IT BACK, WHICH IT DID NOT UNTIL THE TWO VERBS WERE
    MEASURED AGAINST EACH OTHER.** ``run``'s help says it ships this working tree; ``shell``
    said nothing about a tree and shipped none, and the session it opened stood in the Systems
    Manager agent's own directory. So the person who debugged something at a prompt and then
    scripted it with ``run`` was working on two different machines' worth of state: different
    files, a different directory, a different shell and a different ``PATH``. The three
    ``cwd``-shaped lines below are the same three ``run`` performs, in the same order, and
    ``tests/test_lane_environment.py`` holds them to it.
    """
    configuration = _configuration(arguments)
    if after_the_dashes:
        print(_shell_takes_no_command(after_the_dashes), end="", file=err)
        return EXIT_UNUSABLE
    session = _lane_session(arguments, configuration, runner=runner, err=err)
    if isinstance(session, int):
        return session

    settings = session.settings
    project = session.request.project
    uri = working_uri(person=session.request.person, project=project)
    print("\n".join(_wrapped(session.expiry.said(session.machine), indent="")), file=out)
    if arguments.notebook:
        # THE FORWARD SHIPS NOTHING AND SAYS SO, WHICH IS NOT AN INCONSISTENCY WITH THE BRANCH
        # BELOW. It opens a tunnel and runs nothing on the machine, and the Jupyter it reaches
        # is one the researcher started themselves -- from a shell, which is the branch that
        # does the shipping. Syncing here would mean a second session on the way to a port
        # forward, to fetch a tree for a process that is already running.
        print(
            "\n".join(
                _wrapped(
                    f"Anything you want to keep goes in {uri}, which survives the machine. "
                    "Nothing here is recorded as citable, and nothing was checked.",
                    indent="",
                )
            ),
            file=out,
        )
        print("\n".join(_wrapped(what_the_machine_carries(), indent="")), file=out)
        print(
            "\n".join(
                _wrapped(
                    f"Open http://localhost:{LOCAL_NOTEBOOK_PORT}/ once Jupyter is up on the "
                    "machine. Nothing is listening anywhere else: the connection goes through "
                    "Systems Manager, so the notebook is not on the internet. Ctrl-C closes it.",
                    indent="",
                )
            ),
            file=out,
        )
    else:
        print(
            "\n".join(
                _wrapped(
                    f"This directory goes to {uri} and lands in {work_directory(project)} on "
                    "the machine, which is where the shell opens. What is there when you "
                    "leave is carried back. Nothing here is recorded as citable, and nothing "
                    "was checked.",
                    indent="",
                )
            ),
            file=out,
        )
        print("\n".join(_wrapped(what_the_machine_carries(), indent="")), file=out)
        print(
            "\n".join(
                _wrapped(
                    "For an editor over SSH, put this in your ssh config for a host of any "
                    "name, then point the editor at that host:",
                    indent="",
                )
            ),
            file=out,
        )
        print(f"\n  {ssh_proxy_command(session.machine, system=platform.system())}\n", file=out)
    print(file=out)

    # THE SAME UPLOAD ``run`` MAKES, WITH THE SAME EXCLUSION, BEFORE THE SESSION OPENS. It is
    # skipped for the forward, which reaches no shell and no directory.
    if not arguments.notebook:
        runner(
            ("aws", "s3", "sync", str(cwd), uri, "--exclude", ".git/*", "--only-show-errors"),
            env=session.environment,
        )

    # EVERYTHING THIS VERB HAS TO SAY IS SAID BEFORE THE CHILD IS STARTED, AND THEN PUSHED OUT.
    # From here the child owns the terminal and writes to the same descriptors directly, so
    # anything still sitting in this process's buffer would surface in the middle of the
    # researcher's session, or after it.
    out.flush()
    err.flush()

    try:
        runner(
            notebook_forward_argv(
                session.machine, settings=settings, local_port=LOCAL_NOTEBOOK_PORT
            )
            if arguments.notebook
            else shell_session_argv(session.machine, uri=uri, project=project),
            env=session.environment,
            # **THE WHOLE OF WHAT MAKES THIS VERB A TERMINAL RATHER THAN A TRANSCRIPT.** Without
            # it the runner captures both streams and this printed them once the child was gone:
            # a researcher saw an empty screen for as long as they sat there, typed into it
            # blind, and met their whole session at the end. Nobody had ever run this verb
            # through to a usable prompt on any platform.
            #
            # It carries the forwarded notebook too, and there the same capture was worse: that
            # session never exits on its own, so the plugin's "Waiting for connections" -- the
            # one line saying the tunnel is up and the browser is worth opening -- was held
            # behind a pipe until Ctrl-C, and then printed as the person walked away.
            hands_over_the_terminal=True,
        )
    except KeyboardInterrupt:
        # CTRL-C IS HOW BOTH OF THESE END, SO IT IS NOT AN INTERRUPTION AND IS NOT REPORTED AS
        # ONE. It is what the notebook branch tells the person to type four lines above, and it
        # is how anybody leaves a forwarded port. The handler in `main` would otherwise answer
        # 130 and say that nothing is running that this started -- in the one verb where a GPU
        # machine is running and billing, which makes the reassurance false as well as wrong.
        # The expiry printed at the top is what the person actually needs, and they have it.
        pass
    # AFTER THE SESSION AND NOT INSIDE IT, WHICH IS THE ONLY MOMENT A SHELL HAS. ``run`` syncs
    # back when its command returns; a shell has no such moment until the researcher leaves, and
    # this is it -- including after Ctrl-C, which is why it is below the handler rather than in
    # the ``try``. Without it, standing somebody in /work/<project> would be a trap: the
    # paragraph above says what is there is carried back, and an hour of work would go with the
    # machine.
    if not arguments.notebook:
        carried = runner(
            remote_command_argv(
                session.machine, command=carry_back_script(uri=uri, project=project)
            ),
            env=session.environment,
            stdin_stays_open=True,
        )
        if not carried.ok:
            print(_nothing_was_carried_back(uri, project), end="", file=err)
            return EXIT_UNREACHABLE
    # THE SESSION'S OWN EXIT STATUS IS NOT THE RESEARCHER'S VERDICT AND IS NOT REPORTED AS ONE.
    # A shell that the person left with Ctrl-D and a shell they left after a failed command exit
    # the same way, and neither is a statement about anything. run reads a sentinel because it
    # asked one question; this asked none.
    return EXIT_OK


def _nothing_was_carried_back(uri: str, project: str) -> str:
    """**THE ONE THING THIS VERB REPORTS A FAILURE FOR, BECAUSE WORK IS AT STAKE.**

    Everything else a shell does is the researcher's business and its exit status says nothing.
    This is different: the paragraph printed on the way in promised that what is in the work
    directory when they leave is carried back, and if the carry failed that promise is false
    while the machine is still ticking towards its expiry. It names the directory and the
    address so the person can do it themselves from another shell, which costs one command and
    is the whole remedy.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                f"the session ended but {work_directory(project)} could not be carried back to "
                f"{uri}, so anything you did in there is on the machine and nowhere else. The "
                "machine is still running until its expiry. edullm shell again and "
                f"`aws s3 sync . {uri}` from inside it is the remedy.",
                indent="",
            ),
            "",
        ]
    )


def _shell_takes_no_command(given: Sequence[str]) -> str:
    """Somebody typed the wrong verb for what they wanted, and the other one is one word away."""
    return "\n".join(
        [
            "",
            *_wrapped(
                "shell opens a terminal and takes no command, so nothing was started. What you "
                "typed after -- is what run is for, and it ships this directory to the machine "
                "first and streams the output back.",
                indent="",
            ),
            "",
            f"  edullm run --project {'...'} --compute ... -- {' '.join(given)}",
            "",
        ]
    )


# ---------------------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------------------


def _stop(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
) -> int:
    """End the machine this person has for this project, and say what it cost.

    **IT DOES NOT GO THROUGH ``_lane_session``, WHICH IS THE FIRST THING SOMEBODY WILL TRY TO
    TIDY.** That function's job is to hand back a machine, and it starts one where it finds
    none. A verb whose whole purpose is that nothing is billing must not be able to buy an
    instance on a mistyped project, so the two share the identity call and the lane entry and
    nothing after them. ``tests/test_cli_stop.py`` pins that no ``run-instances`` is ever
    reachable from here.

    **IT NEEDS NO SESSION MANAGER PLUGIN, AND THAT IS A PROPERTY RATHER THAN AN OVERSIGHT.**
    Every other lane verb opens a session and refuses without the plugin. This one makes one
    EC2 call and opens nothing, so a laptop whose plugin has broken can still end a machine it
    can no longer connect to -- which is exactly the laptop most likely to need this verb.
    """
    configuration = _configuration(arguments)
    settings = load_working_tier_settings(configuration.directory)
    identity = runner(("aws", "sts", "get-caller-identity", "--output", "json"))
    if not identity.ok:
        print(_no_aws_session(identity.stderr, opens_a_session=False), end="", file=err)
        return EXIT_UNREACHABLE
    facts = json.loads(identity.stdout)
    person = person_from_caller_arn(str(facts["Arn"])) or ""
    project = arguments.project or ""
    refusals = whose_machine_refusals(person=person, project=project)
    if refusals:
        print(render_refusals(refusals), end="", file=err)
        return EXIT_REFUSED

    assumed = runner(
        assume_lane_argv(
            account=str(facts["Account"]),
            project=project,
            person=person,
            # THE SESSION'S DECLARED LIFETIME AND NEVER THE MACHINE'S. The trust policy demands
            # a non-empty ``lifetime`` session tag and this satisfies it; the machine's own
            # lifetime is on its ExpiresAt tag and this verb is about to make it moot. Reading
            # the lane's default keeps the number one that reviewed configuration chose rather
            # than one written here.
            lifetime_hours=load_lane_settings(configuration.directory).default_lifetime_hours,
        )
    )
    if not assumed.ok:
        print(_cannot_enter_the_lane(assumed.stderr), end="", file=err)
        return EXIT_UNREACHABLE
    environment = credentials_environment(json.loads(assumed.stdout)["Credentials"])

    found = runner(find_lane_machines_argv(person=person), env=environment)
    if not found.ok:
        print(_could_not_look_for_a_machine(found.stderr), end="", file=err)
        return EXIT_UNREACHABLE
    machines = lane_machines(found.stdout)
    machine = machine_for_project(machines, project=project)
    if machine is None:
        # EXIT_OK AND NOT A REFUSAL. Nothing has to change: the state this verb exists to
        # produce is already the state. A cleanup verb that exited non-zero on the second call
        # would be one nobody could put in a script, and the mistyped-project case -- the one
        # reading of this that is genuinely wrong -- is answered by naming the projects that do
        # have machines rather than by an exit code nobody reads.
        print(
            "\n".join(_wrapped(no_machine_to_stop(machines, project=project), indent="")), file=out
        )
        return EXIT_OK

    ended = runner(terminate_argv(machine.machine), env=environment)
    if not ended.ok:
        # ALREADY GONE IS NOT A FAILURE, AND THIS IS ONE HALF OF NOT FIGHTING THE JANITOR. The
        # sweep runs every five minutes and the window between this verb's describe and its
        # terminate is open to it. A machine EC2 no longer knows about is a machine that is not
        # billing, which is what was asked for.
        if refusal_code(ended.stderr) == "InvalidInstanceID.NotFound":
            print(
                "\n".join(
                    _wrapped(
                        f"{machine.machine} is already gone -- EC2 no longer has it, so "
                        "something ended it between this command looking and this command "
                        "acting. Nothing of yours is billing for this project.",
                        indent="",
                    )
                ),
                file=out,
            )
            return EXIT_OK
        print(_could_not_end_the_machine(machine.machine, ended.stderr), end="", file=err)
        return EXIT_UNREACHABLE

    for paragraph in what_stopping_did(
        machine,
        now=datetime.now(tz=UTC),
        profile=priced_as(configuration, machine.instance_type),
        # THE MACHINE'S OWN PROJECT TAG AND NOT THE FLAG. They agree here by construction, the
        # flag being what matched it, and reading the tag is what keeps the printed prefix the
        # one this machine actually synced to rather than the one it was asked for.
        uri=working_uri(person=person, project=machine.project),
        object_expiry_days=settings.object_expiry_days,
    ):
        print("\n".join(_wrapped(paragraph, indent="")), file=out)
        print(file=out)
    return EXIT_OK


def _studio(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
) -> int:
    """Open this person's Studio space, or stop it, having first said what it costs.

    **IT ENTERS THE LANE THE WAY EVERY OTHER AWS-TOUCHING VERB DOES.** The researcher role's
    own policy allows ``Action: "*"`` narrowed by seven denies, none of which names a
    SageMaker Studio action, and ``InternSandboxBoundary`` v5 is an ``AdminCeiling`` allow
    with denies that do not either -- so every call below is permitted without an IAM change.
    That was measured with ``iam simulate-principal-policy`` against the deployed role rather
    than reasoned about, because reasoning about an effective policy is how people end up
    building against permissions that do not exist.

    **IT DOES NOT GO THROUGH ``_lane_session``, FOR ``_stop``'S REASON AND ONE MORE.** That
    function starts an EC2 instance where it finds none, and it demands the Session Manager
    plugin. Studio is reached through a browser, so a laptop with no plugin can use this verb,
    which is most of why Studio is the exploration surface at all.

    **THE ORDER OF THE FOUR CALLS IS THE VERB.** Price, then profile, then space, then app.
    Everything before the app is free and idempotent, so a first invocation sets somebody up
    without anybody doing it by hand, and the one call that costs money happens last and after
    the rate has already been printed.
    """
    configuration = _configuration(arguments)
    settings = load_studio_settings(configuration.directory)
    wanted = arguments.instance_type
    shape = shape_for(settings, wanted)
    if shape is None:
        # BEFORE THE IDENTITY CALL, BECAUSE IT NEEDS NEITHER A NETWORK NOR A CREDENTIAL. A
        # misspelled shape answered with "log in first" is a refusal about the wrong thing.
        unpriced: tuple[Refusal, ...] = (unpriced_shape(settings, str(wanted)),)
        if arguments.json:
            emit(refusal_document("studio", unpriced), out=out)
        else:
            print(render_refusals(unpriced), end="", file=err)
        return EXIT_REFUSED

    identity = runner(("aws", "sts", "get-caller-identity", "--output", "json"))
    if not identity.ok:
        print(_no_aws_session(identity.stderr), end="", file=err)
        return EXIT_UNREACHABLE
    facts = json.loads(identity.stdout)
    person = person_from_caller_arn(str(facts["Arn"])) or ""
    request = StudioRequest(
        person=person,
        studio_name=studio_name_for(person),
        # ``--stop`` NEEDS NO PROJECT AND THE PLACEHOLDER IS WHAT SAYS SO. The space is the
        # caller's own and there is one of them, so nothing about stopping it depends on what
        # it was for. Standing a value in here rather than making the field optional keeps
        # ``studio_refusals`` a function of one shape instead of two.
        project=arguments.project or ("--stop" if arguments.stop else ""),
    )
    refusals = studio_refusals(request)
    if refusals:
        if arguments.json:
            emit(refusal_document("studio", refusals), out=out)
        else:
            print(render_refusals(refusals), end="", file=err)
        return EXIT_REFUSED

    assumed = runner(
        assume_lane_argv(
            account=str(facts["Account"]),
            project=request.project,
            person=request.person,
            lifetime_hours=load_lane_settings(configuration.directory).default_lifetime_hours,
        )
    )
    if not assumed.ok:
        print(_cannot_enter_the_lane(assumed.stderr), end="", file=err)
        return EXIT_UNREACHABLE
    environment = credentials_environment(json.loads(assumed.stdout)["Credentials"])

    described = runner(describe_app_argv(settings=settings, request=request), env=environment)
    app = running_app(described.stdout) if described.ok else None

    if arguments.stop:
        return _stop_the_studio_app(
            request,
            settings=settings,
            app=app,
            shape=shape,
            runner=runner,
            environment=environment,
            out=out,
            err=err,
            as_document=bool(arguments.json),
        )
    return _open_the_studio_space(
        request,
        settings=settings,
        app=app,
        shape=shape,
        runner=runner,
        environment=environment,
        out=out,
        err=err,
        as_document=bool(arguments.json),
    )


def _stop_the_studio_app(
    request: StudioRequest,
    *,
    settings: StudioSettings,
    app: RunningApp | None,
    shape: StudioShape,
    runner: CommandRunner,
    environment: dict[str, str],
    out: TextIO,
    err: TextIO,
    as_document: bool,
) -> int:
    """End the compute and keep the disk, which is the verb's whole reason for existing.

    **EXIT_OK WHERE THERE IS NOTHING TO STOP**, which is ``_stop``'s ruling and holds here for
    the same reason: the state this exists to produce is already the state, and a cleanup
    command nobody can run twice is one nobody puts in a script. The refusal's *code* is still
    published under ``--json``, because "there was nothing running" and "I stopped something"
    are different facts to a program even where they are the same outcome to a person.
    """
    if app is None or not app.is_billing:
        told = nothing_to_stop(request)
        if as_document:
            emit(
                {
                    **envelope("studio"),
                    **studio_document(request=request, settings=settings, shape=shape, app=app),
                    "stopped": False,
                    "refused": False,
                    "refusals": [{"code": told.code, "detail": told.detail}],
                },
                out=out,
            )
        else:
            print("\n".join(_wrapped(told.detail, indent="")), file=out)
        return EXIT_OK

    ended = runner(delete_app_argv(settings=settings, request=request), env=environment)
    if not ended.ok:
        print(_could_not_stop_the_studio_app(request, ended.stderr), end="", file=err)
        return EXIT_UNREACHABLE

    monthly = settings.volume_gib_month_usd * settings.volume_gib
    said = (
        f"Stopped the app on {request.studio_name}. The hourly charge has ended. Your files "
        f"are on the space's {settings.volume_gib} GB volume and are not affected, and that "
        f"volume goes on costing about ${monthly:.2f} a month. Running edullm studio again "
        "brings the same disk back under a new app."
    )
    if as_document:
        emit(
            {
                **envelope("studio"),
                **studio_document(request=request, settings=settings, shape=shape, app=app),
                "stopped": True,
                "said": said,
                "refused": False,
                "refusals": [],
            },
            out=out,
        )
    else:
        print("\n".join(_wrapped(said, indent="")), file=out)
    return EXIT_OK


def _open_the_studio_space(
    request: StudioRequest,
    *,
    settings: StudioSettings,
    app: RunningApp | None,
    shape: StudioShape,
    runner: CommandRunner,
    environment: dict[str, str],
    out: TextIO,
    err: TextIO,
    as_document: bool,
) -> int:
    """Start or resume, and hand back a link. The price is printed before anything is bought.

    **A RUNNING APP IS ANSWERED WITH ITS LINK AND NEVER WITH A SECOND APP.** Studio permits
    more than one app on a space, so "start or resume" read carelessly is a second instance on
    the same person's name, billing beside the first, with nothing in either the console or
    this verb saying which one anybody is looking at.
    """
    print("\n".join(_wrapped(price_said(shape, settings), indent="")), file=err)
    print(file=err)

    if app is None or not app.is_billing:
        # ASKED FOR BEFORE ANYTHING IS CREATED, BECAUSE THE SPACE NEEDS IT TOO AND A LOOKUP
        # THAT FAILS HALFWAY WOULD LEAVE A PROFILE AND NO SPACE.
        published = runner(image_account_argv(), env=environment)
        if not published.ok or not published.text:
            unreadable: tuple[Refusal, ...] = (could_not_resolve_the_image(),)
            if as_document:
                emit(refusal_document("studio", unreadable), out=out)
            else:
                print(render_refusals(unreadable), end="", file=err)
            return EXIT_UNREACHABLE
        image = image_arn_for(settings, shape, account=published.text)
        # THE PROFILE AND THE SPACE ARE CREATED BEFORE THE APP AND BOTH ARE FREE. Neither call
        # allocates an instance, so the ordinary first invocation sets somebody up entirely and
        # the only thing they had to know was the verb.
        made = _ensure_the_studio_space(
            request,
            settings=settings,
            shape=shape,
            image_arn=image,
            runner=runner,
            environment=environment,
            err=err,
        )
        if made is not None:
            return made
        print("\n".join(_wrapped(unstopped_said(), indent="")), file=err)
        print(file=err)
        started = runner(
            create_app_argv(settings=settings, request=request, shape=shape, image_arn=image),
            env=environment,
        )
        if not started.ok:
            print(_could_not_start_the_studio_app(request, started.stderr), end="", file=err)
            return EXIT_UNREACHABLE

    signed = runner(presigned_url_argv(settings=settings, request=request), env=environment)
    if not signed.ok:
        print(_could_not_sign_in_to_studio(request, signed.stderr), end="", file=err)
        return EXIT_UNREACHABLE

    if as_document:
        # THE URL IS NOT IN THE DOCUMENT AND ``cli/studio.py`` SAYS WHY. It is a bearer
        # credential with a five-minute life, and a document is the thing somebody redirects
        # into a file and pastes into an issue.
        emit(
            {
                **envelope("studio"),
                **studio_document(request=request, settings=settings, shape=shape, app=app),
                "stopped": False,
                "refused": False,
                "refusals": [],
            },
            out=out,
        )
        return EXIT_OK
    if app is not None and app.is_billing:
        print(already_running_said(app, url=signed.text), file=out)
    else:
        print(signed.text, file=out)
    return EXIT_OK


def _ensure_the_studio_space(
    request: StudioRequest,
    *,
    settings: StudioSettings,
    shape: StudioShape,
    image_arn: str,
    runner: CommandRunner,
    environment: dict[str, str],
    err: TextIO,
) -> int | None:
    """Make the user profile and the space where they do not exist, or say why that failed.

    ``None`` for "there is a space now", which covers both the first invocation and every one
    after it. Create is attempted only where describe says there is nothing, rather than
    attempted-and-forgiven, so a ``ResourceInUse`` from this is a real collision rather than
    the ordinary path.
    """
    profile = runner(
        describe_user_profile_argv(settings=settings, request=request), env=environment
    )
    if not profile.ok:
        made = runner(create_user_profile_argv(settings=settings, request=request), env=environment)
        if not made.ok:
            print(_could_not_make_a_studio_space(request, made.stderr), end="", file=err)
            return EXIT_UNREACHABLE
    space = runner(describe_space_argv(settings=settings, request=request), env=environment)
    if not space.ok:
        made = runner(
            create_space_argv(settings=settings, request=request, shape=shape, image_arn=image_arn),
            env=environment,
        )
        if not made.ok:
            print(_could_not_make_a_studio_space(request, made.stderr), end="", file=err)
            return EXIT_UNREACHABLE
    return None


def _could_not_make_a_studio_space(request: StudioRequest, said: str) -> str:
    """Setting somebody up failed, so nothing was started and nothing is billing."""
    return "\n".join(
        [
            "",
            *_wrapped(
                f"SageMaker would not create the space {request.studio_name}, so nothing was "
                "started and nothing is billing. That is a call that failed rather than "
                f"anything about what you typed. What AWS said: {said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _could_not_start_the_studio_app(request: StudioRequest, said: str) -> str:
    """The one call that costs money did not go through, which is the cheap failure."""
    return "\n".join(
        [
            "",
            *_wrapped(
                f"SageMaker would not start an app on {request.studio_name}, so nothing is "
                "billing by the hour. Your space and its volume are there either way. What "
                f"AWS said: {said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _could_not_sign_in_to_studio(request: StudioRequest, said: str) -> str:
    """A link that could not be minted, over an app that may well be running.

    **IT NAMES ``--stop`` AND THAT IS THE POINT OF THE SENTENCE.** This is the one failure
    here that can leave an instance billing with no way in, so a message that stopped at "the
    URL failed" would leave somebody paying for a machine they cannot reach and cannot see.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                "SageMaker would not mint a sign-in URL. If an app was started just now it is "
                "running and it is billing, so run edullm studio --stop if you do not want it. "
                f"What AWS said: {said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _could_not_stop_the_studio_app(request: StudioRequest, said: str) -> str:
    """Stopping failed, which is the failure worth being blunt about: it is still costing."""
    return "\n".join(
        [
            "",
            *_wrapped(
                f"SageMaker would not stop the app on {request.studio_name}, so it is still "
                "running and still billing by the hour. Running this again is the remedy. "
                f"What AWS said: {said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _could_not_look_for_a_machine(said: str) -> str:
    """EC2 would not say what this person has, so nothing was ended.

    Separate from the message below, because the two leave different worlds behind. This one
    acted on nothing and whatever is running is still running; that one names a machine that is
    still billing and has to be dealt with.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                "EC2 would not say which machines you have, so nothing was ended and anything "
                "you had is still running. That is a call that failed rather than anything "
                f"about what you typed, and running this again is the remedy. What AWS said: "
                f"{said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _could_not_end_the_machine(machine: str, said: str) -> str:
    """**THE ONE MESSAGE HERE THAT HAS TO NAME A MACHINE, BECAUSE ONE IS STILL BILLING.**

    The same rule :func:`_machine_never_answered` follows. Somebody who typed this verb did so
    to stop paying for something, and a refusal that did not name what is still running would
    leave them with the charge and no id. The expiry is what bounds it, so it is said: the
    janitor stops this machine at its ExpiresAt tag whatever happens to this command.
    """
    return "\n".join(
        [
            "",
            *_wrapped(
                f"{machine} could not be ended, so it is still running and still billing. Its "
                "expiry tag still holds, so the janitor stops it on schedule whatever happens "
                "next, and running this again is worth trying first. What AWS said: "
                f"{said.strip()}",
                indent="",
            ),
            "",
        ]
    )


def _scaffolded_said(written: Path) -> str:
    """Where the file went, and what to do with it.

    **IT USED TO POINT AT A REFUSAL AND THE REFUSAL WAS THE DEFECT.** The second sentence
    here read "it is not in any commit yet, which is what the uncommitted_changes refusal
    below is naming", which was an accurate description of a loop: the refusal offered
    stashing, stashing deleted the file, and the next ``check`` wrote it back and refused
    the same way. ``working_tree_refusals`` no longer counts a spec nobody has committed, so
    there is no refusal below to point at and this says the useful thing instead.

    The wording is provisional. It states one fact -- the file is yours and is worth
    committing -- and somebody rewriting these strings should keep the fact and improve the
    sentence.
    """
    return "\n".join(
        [
            f"wrote {written}",
            *_wrapped(
                "It holds the command and the workload profile, which are properties of "
                "the code, so commit it on a branch and push. Nothing here waits on that: "
                "a spec that is in no commit is not counted against this check."
            ),
        ]
    )


@dataclass(frozen=True)
class _SpecInHand:
    """The spec ``check`` is about to price, where it came from, or why there is none.

    ``path`` and ``written`` are not the same question and collapsing them is what this
    dataclass exists to stop. ``written`` is "this invocation created it", which is what the
    ``wrote`` line reports; ``path`` is "this is the file being priced" whether it was found
    or written, and it is what ``working_tree_refusals`` needs in order to tell a spec nobody
    has committed from an edit to one the repository carries.
    """

    spec: RunSpec | None = None
    path: Path | None = None
    written: Path | None = None
    unscaffoldable: Refusal | None = None


def _spec_for_checking(
    arguments: argparse.Namespace,
    configuration: ReviewedConfiguration,
    facts: GitFacts,
    *,
    cwd: Path,
) -> _SpecInHand:
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
        return _SpecInHand(spec=load_spec(declared), path=declared)
    found = find_spec(cwd)
    if found is not None:
        return _SpecInHand(spec=load_spec(found), path=found)
    if facts.root is None or facts.repository is None:
        return _SpecInHand()
    unscaffoldable = _nothing_to_scaffold(arguments, configuration, repository=facts.repository)
    if unscaffoldable is not None:
        return _SpecInHand(unscaffoldable=unscaffoldable)
    written = scaffold_spec(
        configuration,
        repository=facts.repository,
        root=facts.root,
        workload_profile=arguments.workload or None,
        compute_profile=arguments.compute or None,
    )
    return _SpecInHand(spec=load_spec(written), path=written, written=written)


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
                f"run edullm add repository --reason '<why>' to register {repository!r}, "
                "which prepares the pull request that does it. config/repositories.yaml "
                f"carries no entry for it, so nothing here can be submitted and no "
                f"{SPEC_PATH} was written. Registered today: {registered}."
            ),
        )
    if arguments.workload or workloads_registered_for(configuration, repository):
        return None
    return Refusal(
        code="no_workload_profile_registered",
        detail=(
            f"config/workload-catalog.yaml names no workload profile for {repository!r}, so "
            f"a first {SPEC_PATH} would have nothing to point at and no run can name this "
            "repository yet. Adding one is a pull request against the platform."
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
    facts = read_git_facts(runner, cwd=cwd, submitting=arguments.commit)
    submitter = github_login(runner, allow_network=True)
    declared = arguments.spec if arguments.spec else find_spec(cwd)
    spec = load_spec(declared) if declared is not None else None
    preflight = _preflight(arguments, configuration, facts, spec, submitter, spec_path=declared)

    if preflight.refused and not arguments.force:
        # THE VERB, SO THE LINE A READER COPIES IS THE COMMAND THEY RAN. The fields block
        # composes one invocation carrying every flag nothing has answered, and offering
        # ``edullm check`` to somebody who typed ``submit`` would make them edit it first.
        print(render_refusals(preflight.refusals, verb="submit"), end="", file=err)
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
    _say_whether_this_edullm_is_current(runner, repository=arguments.platform_repository, err=err)
    dispatched_at = datetime.now(UTC)
    actions.dispatch(
        SUBMIT_WORKFLOW,
        _submission_form(preflight.request, edullm_version=installed_version().version),
        courtesy=(EDULLM_VERSION_FIELD,),
    )
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
    # SAID BEFORE THE WAIT, FOR THE REASON ``_drive_the_run_report`` SAYS IT BEFORE ITS OWN.
    # This is the only other place the binary makes anybody wait, and it is a wait long enough
    # that a reader who was told nothing concludes it hung.
    #
    # The typical duration is deliberately not here. It was, and
    # ``test_no_bound_is_written_into_a_string_the_cli_prints`` refused it, which is the right
    # refusal twice over: a runner's speed is not this repository's to promise, and a reader
    # given a typical duration reads the ceiling as the anomaly rather than as the bound. The
    # ceiling is the only number the code can stand behind, and it is derived.
    print(
        "\n".join(
            _wrapped(
                "waiting for the compile job to mint the run id, and giving up after "
                f"{_submit_ceiling_said()}. A line every minute says it is still waiting, "
                "and --no-wait skips this.",
                indent="",
            )
        ),
        file=err,
    )
    waiting = _SignOfLife(err)
    outcome = actions.wait_for_the_compiled_submission(identifier, waiting=waiting)
    compiled = outcome.compiled
    if compiled is None and outcome.published_nothing:
        print(
            "the workflow finished and published no compiled submission, so no run id was "
            "minted. The page above carries which job stopped it.",
            file=out,
        )
        return EXIT_OK
    if compiled is None:
        print("\n".join(_wrapped(_no_run_id_yet_said(outcome.status), indent="")), file=out)
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


def _no_run_id_yet_said(status: str | None) -> str:
    """What the wait actually ended on, which is two states and used to be one word.

    THIS SAID "compiling." WHATEVER THE RUN WAS DOING, AND ON 2026-08-06 IT WAS QUEUED.
    GitHub Actions was not starting runs, both jobs read ``queued`` with no conclusion, and
    a first-time submitter read the word and concluded their run had started. The word was
    wrong on any slow queue and the outage only made it likely to be seen.

    ``status`` is the run's own, read by :meth:`~edullm_platform.cli.actions
    .PlatformActions.wait_for_the_compiled_submission` on every poll to learn whether the
    run had finished, so naming the state costs no request. ``queued`` is the one state
    worth its own sentence: nothing has started, so nothing is compiling, and the thing to
    wait on is a runner rather than a job.

    NOTHING ELSE IS NAMED "compiling" EITHER, AND THAT IS DELIBERATE RATHER THAN TIMID. The
    run's status is ``in_progress`` from the moment ``identify`` starts, and ``compile``
    needs ``identify`` and ``resolve`` before it -- so a run that is running is not
    necessarily a run that is compiling, and the per-job answer that would settle it costs a
    second request for a word. What is known is that the job has published nothing yet, so
    that is what is said.
    """
    if status == "queued":
        return (
            "queued. GitHub has not started this run, so nothing is compiling and no run id "
            "has been minted. The submission is dispatched and intact; what it is waiting "
            "for is a runner. The page above says the same thing, and edullm status will "
            "carry the run id once the compile job has run."
        )
    return (
        "no run id yet. The run id is issued by the compile job, which has not published "
        "one; edullm status will carry it once that job has finished."
    )


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


def _submission_form(request: SubmissionRequest, *, edullm_version: str | None) -> dict[str, str]:
    """``SubmissionInputs`` field for field, plus the one input that is not one of them.

    ``image_digest`` is deliberately absent rather than empty. The workflow derives it from
    the declared commit and the field survives only as an override for a deliberate
    rebuild-and-pin; sending a value this binary made up would be that override, aimed at
    nothing.

    ``edullm_version`` is passed in rather than read here, so that both answers are
    reachable from a test. Its absence is the ordinary state of a maintainer's editable
    install and it is left off the form entirely rather than sent empty, because an empty
    string and a missing field mean the same thing on the far side and only one of them
    invites somebody to parse it.
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
        # Rejoined the way the workflow will split it, which is with the quoting on. The
        # compile job POSIX-splits this field, so ``shlex.join`` round-trips: it reproduces
        # the spec's own text and the split on the far side recovers the same words. A plain
        # ``" ".join`` does not. It drops the quotes that group ``bash -lc``'s program into
        # one word, so a three-word command arrives as five and the compile job refuses it
        # after a clean local check. Every ``bash -lc`` command the guides document, and the
        # one ``edullm check`` scaffolds for OLMo-core, was refused that way.
        "command": shlex.join(request.command),
    }
    if request.maximum_runtime_hours is not None:
        fields["maximum_runtime_hours"] = format(request.maximum_runtime_hours, "f")
    if request.maximum_attempts is not None:
        fields["maximum_attempts"] = str(request.maximum_attempts)
    if request.fanout_size is not None and request.fanout_index_parameter is not None:
        fields["fanout_size"] = str(request.fanout_size)
        fields["fanout_index_parameter"] = request.fanout_index_parameter
    # WHICH INSTALL TYPED THIS, WHICH IS NOT A FIELD OF THE SUBMISSION AND IS SENT ANYWAY.
    # A submission the compile job refuses cannot say whether the thing it is refusing was
    # typed that way or was altered on the way here, and the defect of 2026-08-06 was
    # exactly the second: this function joined the command with a plain space, the quotes
    # came off, and the refusal told the submitter to quote what they had already quoted.
    # Nothing gates on the value -- see src/edullm_platform/client_version.py -- and
    # `dispatch` is told it may drop this field rather than fail over it.
    if edullm_version is not None:
        fields[EDULLM_VERSION_FIELD] = edullm_version
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
        if arguments.json:
            emit(status_listing_document(runs), out=out)
            return EXIT_OK
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
        if arguments.json:
            emit(refusal_document("status", [refusal]), out=out)
        else:
            print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED

    _said_resolving(arguments.run_id, err)
    facts = read_run_facts(actions, arguments.run_id)
    if not facts.was_found and not arguments.ask_aws:
        if arguments.json:
            emit(refusal_document("status", [_unfindable_run_id(facts)]), out=out)
        else:
            print(render_refusals([_unfindable_run_id(facts)]), end="", file=err)
        return EXIT_REFUSED
    if arguments.json:
        # NO DISPATCH HERE AND NONE COMING, WHICH IS THE ONE SURPRISE IN THIS FLAG.
        # What a dispatch buys is a section of markdown scraped out of a job log, and
        # publishing that as a field would invent a shape rather than serialize one. So the
        # document carries needs_a_dispatch and stops, and the same verb without --json is
        # what spends the runner. It also keeps --json free, which matters where the caller
        # is a loop rather than a person.
        emit(status_document(facts), out=out)
        return EXIT_OK
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
                            f"{facts.run_id} has no Batch job to stop, so stop the "
                            "submission on GitHub "
                            + (
                                f"instead: gh run cancel "
                                f"{facts.submission.workflow_run_id} --repo "
                                f"{actions.repository}."
                                if facts.submission is not None
                                else "instead, on the workflow run's own page."
                            )
                            + f" {facts.because} Left alone, an approval would still "
                            "start it."
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


# ---------------------------------------------------------------------------------------
# add, ask
# ---------------------------------------------------------------------------------------


def _add(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
    cwd: Path,
    dispatched: list[str],
) -> int:
    """Teach the platform a thing, or say which route this kind goes by instead.

    The routed refusal is answered before anything is read, which is deliberate. Four of the
    five kinds cost no network to refuse and the fifth costs a dispatch, so an agent that
    asks for the wrong one pays nothing at all.
    """
    if arguments.kind not in SELF_SERVICE_KINDS:
        refusal = routed_to_ask(arguments.kind)
        if arguments.json:
            emit(refusal_document("add", [refusal]), out=out)
        else:
            print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED

    facts = read_git_facts(runner, cwd=cwd)
    refusals = _registration_refusals(arguments, facts)
    if refusals:
        if arguments.json:
            emit(refusal_document("add", refusals), out=out)
        else:
            print(render_refusals(refusals), end="", file=err)
        return EXIT_REFUSED

    repository = arguments.repository or facts.repository or ""
    actions = PlatformActions(
        runner, repository=arguments.platform_repository, dispatched=dispatched
    )
    identifier = actions.repository_id(repository)
    dispatched_at = datetime.now(UTC)
    actions.dispatch(
        REGISTER_WORKFLOW,
        register_repository_form(
            repository=repository,
            github_repository_id=identifier,
            reason=arguments.reason,
            dockerfile_path=arguments.dockerfile,
            default_branch=facts.branch or "main",
        ),
    )
    print(f"dispatching {REGISTER_WORKFLOW} ... queued", file=out)
    # WHERE THE PULL REQUEST GETS OPENED, SAID HERE BECAUSE HERE IS WHERE THE PERSON IS.
    # The workflow writes the registration and pushes a branch, and stops: the organization
    # forbids Actions from opening a pull request, and the setting that would allow it also
    # allows approving one, which is what protects the files this registration edits. So the
    # last step is a click, and telling somebody about it only in a workflow log would send
    # them from a terminal into the Actions UI to find a link.
    #
    # This costs no second call to GitHub. The branch is derived from the repository name
    # rather than discovered, so the URL is knowable before the run has finished -- which is
    # also why it is qualified with "once the run above is green" rather than presented as
    # live. What this cannot carry is the prefilled body, which the run composes out of the
    # diff it wrote and prints in its own summary.
    compare = registration_compare_url(repository, platform_repository=arguments.platform_repository)
    submitter = github_login(runner, allow_network=True)
    run = actions.wait_for_a_new_run(REGISTER_WORKFLOW, actor=submitter, after=dispatched_at)
    if run is None:
        print(
            "dispatched, and the workflow run it started could not be found within the poll "
            f"window. It is on its way; the {REGISTER_WORKFLOW} page carries the branch it "
            "pushes and the body to paste.",
            file=out,
        )
    else:
        print(str(run.get("html_url") or ""), file=out)
    print(
        "\n".join(
            _wrapped(
                "It writes the registration and pushes it to a branch. It does not open the "
                "pull request, because this organization forbids Actions from opening one. "
                "Once that run is green, open it here, and paste the body the run's summary "
                "prints -- it is too long to carry in the link:",
                indent="",
            )
        ),
        file=out,
    )
    print(compare, file=out)
    print(
        "\n".join(
            _wrapped(
                "Somebody merges that pull request and then deploys. Nothing is registered "
                "until both have happened, and edullm check refuses this repository until "
                "they have.",
                indent="",
            )
        ),
        file=out,
    )
    return EXIT_OK


def _registration_refusals(arguments: argparse.Namespace, facts: GitFacts) -> list[Refusal]:
    """What a registration needs of the place somebody is standing, asked before a dispatch.

    ``working_tree_refusals`` is deliberately not reused. It answers what the *recorded path*
    needs of a checkout, which is a clean tree, a pushed commit and a published image, and
    none of those is true of a repository being registered for the first time. What a
    registration needs is narrower and different: a name that ``config/repositories.yaml``
    can be keyed on, and a sentence saying why.
    """
    refusals: list[Refusal] = []
    if not facts.is_a_repository and not arguments.repository:
        refusals.append(
            Refusal(
                code="not_a_repository",
                detail=(
                    "stand in a checkout, or pass --repository with the name GitHub spells. "
                    "This directory is not inside a git repository, so there is no name to "
                    "register."
                ),
            )
        )
    elif facts.repository is None and not arguments.repository:
        refusals.append(
            Refusal(
                code="no_origin_remote",
                detail=(
                    "pass --repository, or add an origin remote. This clone has none, and "
                    "config/repositories.yaml is keyed on the GitHub name rather than on "
                    "whatever a clone is called locally."
                ),
            )
        )
    if not arguments.reason:
        refusals.append(
            Refusal(
                code="no_registration_reason",
                detail=(
                    "pass --reason, saying why this needs a repository of its own rather "
                    "than a workload profile in one already registered. It goes in a "
                    "comment above the entry and is the only part of the pull request a "
                    "reviewer cannot derive."
                ),
            )
        )
    return refusals


def _ask(
    arguments: argparse.Namespace,
    *,
    runner: CommandRunner,
    out: TextIO,
    err: TextIO,
) -> int:
    """File one ask, with the environment it was made from attached to it.

    The configuration is loaded for one field and it is worth the read. Which reviewed
    configuration answered is the fact behind most refusals that look wrong, and it is the
    one thing a person filing an ask about a refusal cannot be expected to know.
    """
    if not arguments.detail:
        refusal = Refusal(
            code="no_ask_detail",
            detail=(
                "pass --detail with what you want and what you have already tried. One or "
                "two sentences will do, and a title on its own costs the person answering a "
                "round trip."
            ),
        )
        if arguments.json:
            emit(refusal_document("ask", [refusal]), out=out)
        else:
            print(render_refusals([refusal]), end="", file=err)
        return EXIT_REFUSED

    configuration = _configuration(arguments)
    actions = PlatformActions(runner, repository=arguments.platform_repository)
    body = issue_body(
        detail=arguments.detail,
        submitter=github_login(runner, allow_network=True),
        version=installed_version().version,
        config_directory=str(configuration.directory),
        run_id=arguments.run,
    )
    wanted = (ASK_QUEUE_LABEL, arguments.kind)
    url, labelled = actions.create_issue(title=arguments.title, body=body, labels=wanted)
    if not labelled:
        print(
            "\n".join(
                _wrapped(
                    f"filed without the {' and '.join(wanted)} labels, one of which this "
                    f"repository does not carry yet. The count reads {ASK_QUEUE_LABEL} and "
                    "groups by kind, so add both to the issue or to the repository and this "
                    "one joins the count.",
                    indent="",
                )
            ),
            file=err,
        )
    print(url, file=out)
    return EXIT_OK


# ---------------------------------------------------------------------------------------
# shared, for the run verbs
# ---------------------------------------------------------------------------------------


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


def _submit_ceiling_said() -> str:
    """The same, for ``submit``'s own wait, which is a different pair of loops."""
    minutes = -(-int(submit_ceiling_seconds()) // 60)
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
            f"leading {SHORTEST_RUN_ID} characters of the UUID are enough where no two of "
            "your recent runs share them. edullm status lists yours in the short form."
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
            f"{facts.because} Pass --ask-aws to ask anyway, which dispatches "
            f"{CANCEL_WORKFLOW} under the one identity that may read a Batch job, spends a "
            f"runner and gives up after {_ceiling_said()}. Nothing was dispatched here, and "
            "this may well be a real run. Whether it is older than the window or was never "
            "minted cannot be told from a laptop."
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
                f"resolving {run_id}, which takes a few seconds. Nothing indexes run ids, "
                "so this reads the manifest of each recent submission until it knows which "
                "one. An id given in full is found in the first one or two.",
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
    spec_path: Path | None = None,
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

    ``spec_path`` is where the spec in hand came from, and ``working_tree_refusals`` is what
    reads it. **BOTH VERBS PASS IT, WHICH IS THE HALF THAT IS EASY TO LEAVE OUT.** ``check``
    is where the spec gets written and so where the loop was; ``submit`` never scaffolds and
    would therefore have kept refusing exactly what ``check`` had just cleared, which is a
    worse defect than the one being fixed.
    """
    # WHAT THE BUILD READS AND WHAT THE COMMAND NAMES, SO THE TREE CHECK CAN TELL AN
    # UNTRACKED FILE THAT MATTERS FROM ONE THAT CANNOT. Both come out of files this verb has
    # already opened: the Dockerfile path is the registry's, and the command is the spec's.
    # Neither is spelled here, because a repository may register another path and a
    # hardcoded one would refuse the wrong file.
    dockerfile_path = _registered_dockerfile_path(arguments, facts, configuration)
    command = spec.argv if spec is not None else ()
    refusals: list[Refusal] = working_tree_refusals(
        facts, spec_path=spec_path, dockerfile_path=dockerfile_path, command=command
    )
    untracked = untracked_the_image_will_not_see(
        facts, spec_path=spec_path, dockerfile_path=dockerfile_path, command=command
    )
    if unscaffoldable is not None:
        refusals.append(unscaffoldable)
    elif spec is None:
        refusals.append(
            Refusal(
                code="no_run_spec",
                detail=(
                    f"stand in a checkout of a registered repository, where check writes a "
                    f"first {SPEC_PATH} for you. There is none at or above here, and this "
                    "directory is not a checkout it could be written into."
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

    # ``submitted_commit`` rather than ``commit_sha``, because that is the one the request
    # below is built from. Where no ``--commit`` was given the two are the same value.
    if spec is None or team is None or missing or facts.submitted_commit is None:
        # PRICED ANYWAY, WHICH IS THE WHOLE OF WHY THIS BRANCH IS NOT A BARE RETURN. Every
        # field that stops the request being built here -- the team, the experiment, the
        # dataset -- is a field the price does not read, so a verb whose own help says it
        # prices a submission was printing three refusals and no number on the first
        # invocation a researcher makes. What it can answer it answers; what it cannot it
        # leaves ``None`` and the terminal says which.
        partial = _partial_request(arguments, spec, facts, team)
        shape = price_what_is_known(partial, configuration)
        return Preflight(
            request=partial,
            refusals=said_once(refusals),
            team_source=team_source,
            branch=facts.branch,
            workload=shape.workload,
            compute=shape.compute,
            dataset=configuration.datasets.reference_for(partial.dataset_release),
            cost=shape.cost,
            history=shape.history,
            untracked=untracked,
        )

    request = SubmissionRequest(
        repository=arguments.repository or facts.repository or "",
        # ``facts.submitted_commit`` rather than the same expression written out again, so
        # the manifest and the refusals cannot read two different commits. They did: the
        # manifest honoured ``--commit`` and ``commit_not_pushed`` was about HEAD.
        commit_sha=facts.submitted_commit,
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
        refusals=said_once((*refusals, *preflight.refusals)),
        team_source=preflight.team_source,
        branch=facts.branch,
        workload=preflight.workload,
        compute=preflight.compute,
        dataset=preflight.dataset,
        manifest=preflight.manifest,
        cost=preflight.cost,
        approval_class=preflight.approval_class,
        approving_environment=preflight.approving_environment,
        history=preflight.history,
        untracked=untracked,
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
                    "pass --experiment, which names how this run groups with its "
                    "neighbours. It registers nothing, so any lower-case hyphenated name "
                    "will do."
                ),
                asks_for="--experiment",
                example="a-first-run",
            )
        )
    if not arguments.dataset:
        refusals.append(
            Refusal(
                code="no_dataset",
                detail=(
                    "pass --dataset with the corpus this run reads, or --dataset none where "
                    "it reads nothing. Absent and none are different answers and only one "
                    "of them is a statement."
                ),
                asks_for="--dataset",
                # ``none`` and not a release, because the copyable line has to be one a
                # reader may run unchanged, and naming a corpus for somebody is the guess
                # the detail above says the tool must not make.
                example="none",
            )
        )
    if spec is not None and not (arguments.compute or spec.suggested_compute):
        refusals.append(
            Refusal(
                code="no_compute_profile",
                detail=(
                    f"pass --compute, or set suggested_compute in {SPEC_PATH}. It is the "
                    f"most expensive field on a submission, at {_rate_span(configuration)}, "
                    "and there is nothing to derive it from."
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
        return "rates spanning orders of magnitude"
    return f"${plain_decimal(min(rates))} to ${plain_decimal(max(rates))} an hour"


def _registered_dockerfile_path(
    arguments: argparse.Namespace,
    facts: GitFacts,
    configuration: ReviewedConfiguration,
) -> str | None:
    """Where this repository's build recipe lives, or ``None`` where nothing says.

    Read from ``config/repositories.yaml`` rather than written here. Every registered
    repository happens to name ``.edullm/Dockerfile`` today and the field exists so that one
    of them can name something else, so a constant would refuse a file that is not the
    recipe and clear the one that is.

    ``None`` for a directory nothing registers, which is a repository already refused by
    ``unregistered_repository``. Nothing is known about what its build would read, so the
    untracked question falls back to what the command names.
    """
    named = arguments.repository or facts.repository
    if not named:
        return None
    try:
        return configuration.repositories.repository_by_name(named).dockerfile_path
    except UnknownRepositoryError:
        return None


def _partial_request(
    arguments: argparse.Namespace,
    spec: RunSpec | None,
    facts: GitFacts,
    team: str | None,
) -> SubmissionRequest:
    """Whatever is known, so a refusal can still say what it was refusing.

    **THE FOUR BOUNDS ARE HERE BECAUSE THEY ARE FOUR OF THE FIVE FACTORS IN THE PRICE.**
    They were dropped, which was harmless while this request was only ever printed back at
    a reader, and stopped being harmless when ``price_what_is_known`` began reading it: a
    ``--hours 2`` run would have been priced at the profile's twenty-four and a fan-out at
    one cell, so the number would have been wrong rather than absent.
    """
    return SubmissionRequest(
        repository=arguments.repository or facts.repository or "",
        commit_sha=facts.submitted_commit or "",
        workload_profile=arguments.workload or (spec.workload_profile if spec else ""),
        compute_profile=arguments.compute or (spec.suggested_compute if spec else "") or "",
        dataset_release=arguments.dataset or "",
        team=team or "",
        experiment=arguments.experiment or "",
        wandb_project=arguments.wandb_project or team or "",
        command=spec.argv if spec else (),
        maximum_runtime_hours=_decimal_hours(arguments.hours),
        maximum_attempts=arguments.attempts,
        fanout_size=arguments.fanout_size
        or (spec.fanout.size if spec is not None and spec.fanout is not None else None),
        fanout_index_parameter=arguments.fanout_index_parameter
        or (
            spec.fanout.index_parameter
            if spec is not None and spec.fanout is not None
            else None
        ),
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
