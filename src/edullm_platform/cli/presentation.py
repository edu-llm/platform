"""What the terminal shows, held apart from what the checks decide.

THE LAYOUT IS THE ONE IN ``docs-frank/working/terminal-mockups/``, which is the closest
thing to a specification this surface has. Four blocks in a fixed order -- what would be
submitted, what it may cost, who releases it, and what was not checked here -- because a
reader learning the shape once can then find the number they came for without reading the
rest. The refusal form is the other one those transcripts settle: a count, a line saying
nothing was dispatched, and then one block per refusal carrying a code and a remedy.

**Three places the transcripts and the code disagreed, and the code won each time; the
transcripts have since been corrected to match.** The money is printed as the platform's own
arithmetic prints it, quantized to a cent, where ``adarsh-rajesh-first-run.md`` showed a
third decimal -- a CLI that rounded differently from the approver page would have a
submitter and a lead reading two prices for one run. The automatic runtime bound is read
from ``config/policy.yaml`` rather than fixed at the figure
``grant-matherne-scarce-shape-v2.md`` printed, because ``docs-frank/reference/decisions.md``
records that figure as *not ruled*. And no device memory is printed beside a machine: the
transcripts showed a per-node total that lives in a prose table in the overview and in no
file this binary reads, so what is printed is the instance type and the device count, which
are read.

NO POLICY NUMBER IS WRITTEN ANYWHERE IN THIS PACKAGE, AND ``test_cli_no_hardcoded_bounds.py``
is what keeps it that way. Every bound, rate, ceiling and count that reaches a terminal is
interpolated out of the loaded configuration at the moment of printing, so the only way to
change what ``edullm`` says a limit is is to change the file that is the limit. The rule is
structural rather than a habit because the runtime bound has already disagreed between the
documents and the configuration three separate times, and each of those was two copies that
agreed on the day somebody wrote the second one. It reads a number spelled as an English word
now, which it did not, and "any of the nine approvers can release it" is the copy that got in
under it.

AND ONE LINE HERE IS ABOUT THE FILES RATHER THAN THEIR CONTENTS. ``check`` resolves its
configuration by four routes with the packaged copy beating a checkout's ``config/``, and
until :func:`config_source_said` existed nothing printed said which had answered. Two runs
against two configurations were byte-identical, which made the precedence rule invisible to
the one reader placed to notice it had drifted. It is the last line rather than the first
now. What it catches is real and rare, and it was the opening ninety characters of the first
thing a new researcher ever read from this tool: a path to a ``site-packages`` directory,
above the price they came for and above the problem they had. The maintainer chasing a stale
validator finds it wherever it is, because he is the reader who knows to look for it.

REFUSALS GO UNDER WHAT WAS ESTABLISHED RATHER THAN INSTEAD OF IT. Every block above them is
a reading -- the tree that was resolved, the ceiling, what runs of this shape have taken --
and a reading does not stop being true because something else was refused. The verb's own
help says it prices a submission, and it printed no price at all on the first invocation a
researcher makes, because a single early return here replaced the whole report with the
refusal list.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final

from edullm_platform.cli.actions import ADMITTED, RunFacts, elapsed_said
from edullm_platform.cli.configuration import PACKAGED_CONFIG_DIRECTORY, ReviewedConfiguration
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT, Preflight, Refusal
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.authorization import (
    holds_exception_approver_role,
    holds_routine_approver_role,
)
from edullm_platform.contracts.base import serialize_decimal
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy, PolicyThresholds
from edullm_platform.contracts.workload import ComputeProfile, WorkloadProfile
from edullm_platform.execution import CONTAINER_SHAPES

__all__ = [
    "approvers_said",
    "config_source_said",
    "plain_decimal",
    "render_preflight",
    "render_refusals",
    "render_run_facts",
    "render_run_listing",
    "who_may_release",
]

#: Where the second column starts. Wide enough for ``experiment`` and the longest label
#: below it, and narrow enough that a value fits beside it in eighty columns.
LABEL_WIDTH = 18


def render_preflight(preflight: Preflight, *, configuration: ReviewedConfiguration) -> str:
    """The whole of what ``edullm check`` prints, refused or not.

    Takes the whole configuration rather than the policy alone, because two of the things
    printed here are facts about which files answered: who may release at a gate is read off
    the roster, and the first line names the directory all six came out of.
    """
    # One line and never wrapped, unlike every paragraph below it. It is a sentence and a
    # path, the path is the whole point, and ``_wrap`` breaks at spaces -- so on the install
    # this exists for, where the directory is a hundred characters of ``site-packages``, the
    # wrapped form puts "checked against" alone on the first line and helps nobody. A
    # terminal soft-wraps it and a pipe keeps it one line, which is what a reader greps.
    source = config_source_said(configuration.directory)
    blocks = [
        _manifest_block(preflight),
        _cost_block(preflight),
        _history_block(preflight),
        _approval_block(preflight, configuration.policy, configuration.inventory),
        (
            render_refusals(preflight.refusals).rstrip("\n")
            if preflight.refused
            # The deferred pair is what a clean check has to say before somebody reads it as
            # a promise. A refused one has already said nothing was dispatched, and two more
            # codes under a heading about the registry are two more things to read past.
            else _deferred_block()
        ),
        "" if preflight.refused else "no refusals. edullm submit will dispatch this.",
        source,
    ]
    return "\n\n".join(block for block in blocks if block) + "\n"


def config_source_said(directory: Path) -> str:
    """Which reviewed configuration answered, on every ``check`` whether it refused or not.

    **THE ONE PERSON THIS IS FOR IS THE ONE THE TOOL WAS HIDING IT FROM.** ``check`` resolves
    its configuration by four routes and the packaged copy beats a checkout's ``config/``, so
    a maintainer standing in the platform tree is normally validating against the wheel's
    frozen copy rather than against the files he is editing. Before this line the two runs
    printed identical bytes, which made the precedence rule invisible from a terminal to the
    one reader placed to notice it had drifted.

    ON THE REFUSAL PATH TOO, AND THAT IS THE COMMON CASE RATHER THAN THE THOROUGH ONE. A
    stale validator's damage is a refusal that is wrong -- a profile it has not been told was
    promoted, a dataset registered last week -- and a reader deciding whether to believe a
    refusal is exactly the reader who needs to know which files produced it.

    LAST RATHER THAN FIRST, AND THAT IS THE ONLY THING THAT HAS CHANGED ABOUT IT. It led
    every ``check`` for a while, so the first thing a researcher read on their first morning
    was ninety characters of somebody else's install path. The information is worth keeping
    and worth nobody's opening line: the reader it is for is looking for it.

    A path and no colour, like everything else here, so a piped run and a terminal run stay
    the same bytes.
    """
    if directory == PACKAGED_CONFIG_DIRECTORY:
        return f"checked against {directory}, the copy this install carries"
    return f"checked against {directory}"


def who_may_release(
    inventory: OrganizationInventory, environment: ApprovalEnvironment
) -> tuple[str, ...]:
    """The roster entries holding the role this gate asks for, by the platform's own test.

    Filtered through ``holds_routine_approver_role`` and ``holds_exception_approver_role``
    rather than by counting ``admins`` and ``team_leads`` here, because those two functions
    are what admission applies inside AWS. A second reading of the same two lists would agree
    on the day it was written and stop agreeing the moment either role gained a source -- and
    a routine run needs an admin *or* a lead, which is a union nothing in the policy file
    states and which a reader counting one list would get wrong by two.

    Empty for ``run-approval-automatic``. That is a real environment carrying a real branch
    policy and no reviewer, so nobody releases one and the absent count is the answer rather
    than a gap.
    """
    holds = {
        ApprovalEnvironment.LEAD: holds_routine_approver_role,
        ApprovalEnvironment.ADMIN: holds_exception_approver_role,
    }.get(environment)
    if holds is None:
        return ()
    return tuple(
        member.github_login
        for member in inventory.members
        if holds(inventory, member.github_login)
    )


def approvers_said(inventory: OrganizationInventory, environment: ApprovalEnvironment) -> str:
    """How many people may release at this gate, counted rather than written down.

    **THE NUMBER THIS REPLACES WAS RIGHT AT ONE GATE BY COINCIDENCE AND WRONG BY SEVEN AT THE
    OTHER.** Both call sites said "any of the nine approvers can release it" for every
    non-automatic class. Nine is the size of ``admins`` unioned with ``team_leads``, so it
    happened to describe ``run-approval-lead``; ``run-approval-admin`` asks only the admins,
    of whom there are two, and an exception run is disproportionately the owner's because he
    is the one submitting on the expensive shapes. A sentence that is accidentally true of the
    cheap path and false of the expensive one is worse than no sentence.

    **AND WHAT IT SAYS IS THE ROSTER'S ANSWER, NOT THE GATE'S, WHICH IS A DIFFERENT FACT.**
    The reviewed configuration records who holds an approver role and admission enforces
    exactly that. Which accounts the GitHub environment itself lists is a setting in the
    organization -- ``run-approval-lead`` is gated by the ``team-leads`` team -- and it lives
    in no file this repository carries. ``config/organization.yaml`` says so at length and
    records that the two agreed when somebody last checked by hand. So the second sentence
    names the gap rather than letting a count read as a promise about the gate, and this
    stays a pure read of files already loaded: ``check`` answers with no network and must.
    """
    count = len(who_may_release(inventory, environment))
    if not count:
        return (
            f"nobody releases a run at {environment.value}. It carries a deployment branch "
            "policy and no reviewer."
        )
    people = "person holds" if count == 1 else "people hold"
    return (
        f"{count} {people} the role {environment.value} asks for. Which accounts that gate "
        "itself lists is a GitHub setting rather than reviewed configuration, and nothing "
        "here reads it."
    )


def render_refusals(refusals: Sequence[Refusal], *, verb: str = "check") -> str:
    """The refusal form: how many, that nothing moved, then one block each.

    "Nothing was dispatched" is on the first line rather than the last, because a reader
    seeing a wall of red needs to know the run did not start before they need to know why.

    **THE FIELDS NOBODY HAS FILLED IN ARE ONE BLOCK, BECAUSE THEY ARE ONE QUESTION.** A
    first invocation in a registered checkout refuses for the team, the experiment and the
    dataset at once, and three stanzas under three codes read as three things being wrong
    with a checkout that is fine. What is true is that the tool has not been told what the
    run is. They are gathered under one heading with one line that answers all of them, and
    every explanation is printed underneath unchanged: those sentences are the part worth
    keeping, and "absent and none are different answers" is not a thing a reader works out
    from a usage string.
    """
    count = len(refusals)
    noun = "refusal" if count == 1 else "refusals"
    asked = [refusal for refusal in refusals if refusal.asks_for]
    # A COUNT OF REFUSALS IS THE WRONG FIRST LINE WHERE ALL OF THEM ARE ONE QUESTION. "3
    # refusals" above "3 fields, one question" is the arithmetic the block below exists to
    # undo, so where nothing else was refused the count is left to the block and the line
    # says only the thing every refused run has to say first.
    lines = (
        ["Nothing was dispatched.", ""]
        if asked and len(asked) == count
        else [f"{count} {noun}. Nothing was dispatched.", ""]
    )
    if asked:
        lines.extend(_fields_block(asked, verb=verb))
        lines.append("")
    for refusal in refusals:
        if refusal.asks_for:
            continue
        lines.append(f"refused  {refusal.code}")
        lines.extend(f"  {line}" for line in _wrap(refusal.detail))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _fields_block(asked: Sequence[Refusal], *, verb: str) -> list[str]:
    """The one-question block: a heading, a line to copy, then each field's own reasons.

    The copyable line comes before the explanations rather than after them. A reader who
    already knows what a team is wants the line and nothing else; one who does not reads
    on. Putting it last would make the second reader's need decide the first reader's
    scroll.

    The examples in it are placeholders that happen to be runnable, and none of them is a
    choice the tool is making on somebody's behalf: a group the roster already puts them
    on, a name that registers nothing, and ``none`` for the dataset, which is the answer
    that says this run reads no corpus.
    """
    count = len(asked)
    fields = "field" if count == 1 else "fields"
    heading = (
        f"{count} {fields} nothing has answered. They are one question, what is this run, "
        "and one line answers all of them:"
    )
    typed = " ".join(f"{refusal.asks_for} {refusal.example}" for refusal in asked)
    lines = [*_wrap(heading), "", f"  edullm {verb} {typed}", ""]
    for refusal in asked:
        lines.append(f"refused  {refusal.code}")
        lines.extend(f"  {line}" for line in _wrap(refusal.detail))
        lines.append("")
    return lines[:-1]


def render_run_facts(facts: RunFacts) -> str:
    """What GitHub alone could establish about one run, and what it cost, which is nothing.

    ENDS BY SAYING WHICH WAY IT WENT, ALWAYS. A reader who is about to wait needs to know
    they are about to wait and why, and a reader who is not needs to know the answer is
    complete rather than truncated -- "nothing was dispatched to answer this" is the same
    reassurance ``render_refusals`` puts on its first line, for the same reason.
    """
    submission = facts.submission
    lines: list[str] = []
    if submission is not None:
        heading = [submission.short_run_id, submission.state, elapsed_said(submission.created_at)]
        lines += [
            "  ".join(heading),
            "",
            *(_row("experiment", facts.experiment) if facts.experiment else []),
            *(_row("team", facts.team) if facts.team else []),
            *(_row("cells", str(submission.cells)) if submission.cells else []),
        ]
        if facts.gate is not None:
            lines += _row("waiting on", facts.gate)
        if facts.reviewers:
            lines += _row("reviewers", ", ".join(facts.reviewers))
        if facts.you_can_release:
            # The line this whole endpoint is worth reading for. A lead who learns in their
            # own terminal that a run is waiting on them specifically has a reason to run
            # status at all, where "waiting for a lead" is a fact about somebody else.
            lines += _row("you", "can release this. Approve it on the run page.")
        if facts.approver is not None:
            released = facts.approver
            if facts.approved_at is not None:
                released += f", {elapsed_said(facts.approved_at)} ago"
            lines += _row("released by", released)
        if facts.declined is not None:
            # TWO ROWS AND NOT ONE, BECAUSE WHO SAID NO AND WHY ARE DIFFERENT QUESTIONS AND
            # THE SECOND IS OFTEN UNANSWERED. GitHub's box is optional and a decline with no
            # sentence in it is the ordinary case, so the reason row says that rather than
            # being dropped, which would read as a tool that did not look.
            said = facts.declined.by or "somebody this could not name"
            if facts.declined.at is not None:
                said += f", {elapsed_said(facts.declined.at)} ago"
            lines += _row("declined by", said)
            lines += _row("reason", facts.declined.reason or "none given")
        if submission.url:
            lines += _row("run page", submission.url)
        lines.append("")

    lines += _wrap(facts.because)
    lines.append("")
    lines.append(
        "nothing was dispatched to answer this."
        if not facts.needs_a_dispatch
        else "reading that from AWS needs a runner, which is the wait below."
    )
    return "\n".join(lines) + "\n"


def _row(label: str, value: str) -> list[str]:
    return [f"  {label:<{LABEL_WIDTH}}{value}"]


def render_run_listing(rows: Iterable[tuple[str, str, str, str]]) -> str:
    """One line per run, in the column order the transcripts use.

    Run, state, how long it has been in it, and what it is for. The waiting time is third
    because it is the field that changes between two invocations a minute apart, and the
    experiment is last because it is the widest.

    **AND THEN WHERE THIS VIEW STOPS, WHICH IS THE HALF IT USED TO LEAVE A READER TO WORK
    OUT.** Every word in the table is about a submission workflow, and a submitter reads it
    as being about their run. Those agree right up to admission and then part company for
    the hours that matter: on 2026-08-06 eight rows here read the same word, and Batch had
    five of them succeeded, one failed and two it held no record of at all. Renaming that
    word to :data:`~edullm_platform.cli.actions.ADMITTED` stops it claiming an outcome it
    cannot know; the sentence below is what stops the absence of an outcome reading as an
    oversight.
    """
    listed = list(rows)
    if not listed:
        return (
            "no runs. GitHub Actions keeps workflow runs for a bounded window, and nothing "
            "you submitted is still inside it.\n"
        )
    widths = [max(len(row[column]) for row in listed) for column in range(3)]
    lines = [
        f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}".rstrip()
        for row in listed
    ]
    lines.extend(_where_github_stops(listed))
    return "\n".join(lines) + "\n"


def _where_github_stops(listed: Sequence[tuple[str, str, str, str]]) -> list[str]:
    """The boundary sentence, on the listings that have crossed it and not on the others.

    Read off the rows that were just rendered rather than recomputed from the runs, so the
    sentence cannot describe a table this is not printing.

    Silent where nothing reads ``ADMITTED``. A listing of submissions still compiling or
    parked at a gate is wholly about things that are still happening on GitHub, so every
    word of it is true of the run as well, and a warning about a boundary nobody has reached
    is a paragraph a reader learns to skip -- which is how the one that matters gets skipped
    too.
    """
    if not any(row[1] == ADMITTED for row in listed):
        return []
    return [
        "",
        *_wrap(
            f"{ADMITTED} is where GitHub stops knowing. The job reached AWS, and a run that "
            "finished an hour ago reads exactly like one still queued for a machine. "
            "edullm status <run-id> asks AWS what one of them is doing, and spends a "
            "runner to do it."
        ),
    ]


#: What a row says where nothing filled it in. One wording, so that a reader learns once
#: that the tool is reporting an absence rather than having found an empty value.
NOT_GIVEN: Final = "not given"

#: Why a priced run can still have no gate named. The ceiling above it is arithmetic over
#: the machine and the runtime; which gate releases it is a ruling over the whole request,
#: and the fields the refusals below ask for are inputs to that ruling.
_UNDECIDED_APPROVAL: Final = (
    "not decided here. The ceiling above is the machine and the runtime, which are known. "
    "Which gate releases a run is decided from the whole request, and the fields refused "
    "below are part of it. Fill them in and check again."
)


def _manifest_block(preflight: Preflight) -> str:
    """What this invocation resolved, whether or not the rest of it was refused.

    **THE BRANCH AND THE COMMIT ARE HERE SO A READER CAN TELL THIS READ THEIR TREE.** A
    check that names neither is a check somebody has to trust; the walkthroughs that find
    defects in this verb happen on a branch rather than on ``main``, and "did it even look
    at what I am standing in" is the first question a refusal raises.

    Every row is printed even where the value is absent, and an absent one says
    :data:`NOT_GIVEN`. A row that vanished would leave a reader counting fields to work out
    which one the tool never got, which is the same argument ``_history_block`` makes about
    a shape with no history.
    """
    request = preflight.request
    rows: list[tuple[str, str]] = [
        ("repository", request.repository or NOT_GIVEN),
        ("branch", preflight.branch or NOT_GIVEN),
        # Twelve characters, which is what the image tag carries and what every transcript
        # and every approver page prints. The full forty go to the workflow.
        ("commit", request.commit_sha[:12] or NOT_GIVEN),
        (
            "image",
            "resolved at submit, from the commit above",
        ),
    ]
    if preflight.workload is not None:
        rows.append(("workload", _workload_said(preflight.workload)))
    if preflight.compute is not None:
        rows.append(("compute", _compute_said(preflight.compute)))
    rows.append(("dataset", _dataset_said(preflight)))
    if request.fanout_size is not None and request.fanout_index_parameter is not None:
        rows.append(
            (
                "fan-out",
                (
                    f"{request.fanout_size} cells, index parameter "
                    f"{request.fanout_index_parameter!r}"
                ),
            )
        )
    rows.append(("team", _two_columns(request.team or NOT_GIVEN, preflight.team_source)))
    rows.append(("experiment", request.experiment or NOT_GIVEN))
    rows.append(("wandb project", request.wandb_project or NOT_GIVEN))
    if preflight.untracked:
        # SAID BECAUSE THE READER CAN SEE THEM AND THIS NO LONGER REFUSES FOR THEM. A tree
        # with four untracked files that is called clean is a tool that either did not look
        # or is not saying, and both send somebody to read the source. One line, and it is
        # the reason rather than a warning: they are in no commit, and the commit is what
        # becomes the image.
        count = len(preflight.untracked)
        said = (
            "1 file in no commit, so it does not reach the image"
            if count == 1
            else f"{count} files in no commit, so none of them reach the image"
        )
        rows.append(("untracked", said))
    return "manifest\n" + "\n".join(f"  {label:<{LABEL_WIDTH}}{value}" for label, value in rows)


def _cost_block(preflight: Preflight) -> str:
    """The ceiling, the five factors under it, and the lever for each factor that has one.

    THE ATTEMPT FACTOR GETS A LINE BECAUSE THE SUBMITTER IS THE ONE WHO CAN STILL MOVE IT.
    :func:`~edullm_platform.checkpoint_commands.unverified_resume_note` says the same thing
    at length on the approver page and under ``retries`` in ``check --json``, and the person
    reading it there is deciding about somebody else's run with an approval already asked
    for. Here it is being read by whoever chose the count, before anything is dispatched,
    which makes ``--attempts`` a lever rather than a survey -- so this is the fact and the
    lever and none of the argument behind them.
    """
    cost = preflight.cost
    if cost is None:
        return ""
    cells = "cell" if cost.cells == 1 else "cells"
    attempts = "attempt" if cost.maximum_attempts == 1 else "attempts"
    nodes = "node" if cost.nodes == 1 else "nodes"
    lines = [
        f"worst case  ${plain_decimal(cost.maximum_compute_cost_usd)}",
        (
            f"  ${plain_decimal(cost.hourly_rate_usd)}/hour x {cost.nodes} {nodes} x "
            f"{plain_decimal(cost.maximum_runtime_hours)}h x {cost.maximum_attempts} "
            f"{attempts} x {cost.cells} {cells}"
        ),
        "  A ceiling rather than an estimate, and what routes the run. Lowering --hours",
        "  is what moves a run under the automatic bound.",
    ]
    if cost.maximum_attempts > 1:
        lines.extend(
            f"  {line}"
            for line in _wrap(
                "Lower --attempts to 1 if this program does not resume. Nothing here "
                "checks that it does, and an attempt that starts over costs what the "
                "first one did."
            )
        )
    return "\n".join(lines)


def _history_block(preflight: Preflight) -> str:
    """What runs of this shape have taken, printed under the ceiling that overstates it.

    UNDER THE CEILING AND NOT INSTEAD OF IT, WHICH IS THE WHOLE ARRANGEMENT. The worst case
    is what is being authorised and is what routes the run, so it goes first and keeps its
    words. This is what the worst case overstates, it decides nothing, and a reader has both
    numbers rather than a choice between them.

    Printed on every priced submission, including the ones with no history at all. A block
    that vanished when the answer was "nothing has run this" would leave a reader unable to
    tell that from a version of the tool that does not print durations.
    """
    answer = preflight.history
    if answer is None:
        return ""
    return "what it has taken\n" + "\n".join(f"  {line}" for line in _wrap(answer.said))


def _approval_block(
    preflight: Preflight, policy: ApprovalPolicy, inventory: OrganizationInventory
) -> str:
    approval_class = preflight.approval_class
    cost = preflight.cost
    if cost is None:
        # Nothing was priced, so there is no figure for a gate to be about. The refusals
        # below say what stopped it.
        return ""
    if approval_class is None or preflight.approving_environment is None:
        # SAID RATHER THAN LEFT OUT, WHICH IS THE HALF A PRICE ON ITS OWN GETS WRONG. A
        # reader handed a ceiling and no gate has been shown the expensive number with the
        # question of who has to agree to it quietly dropped. Which gate a run reaches is
        # decided from facts a half-described request has not supplied, so this names the
        # gap rather than guessing at the answer.
        return "\n".join(["approval", *(f"  {line}" for line in _wrap(_UNDECIDED_APPROVAL))])
    limits = policy.thresholds
    lines = ["approval"]
    if approval_class is ApprovalClass.AUTOMATIC:
        lines.extend(f"  {line}" for line in _wrap(_automatic_said(limits)))
        return "\n".join(lines)

    lines.append(f"  {approval_class.value} -> {preflight.approving_environment.value}")
    lines.extend(
        f"  {reason}"
        for reason in _why_not_automatic(
            preflight, policy, inventory, preflight.approving_environment
        )
    )
    return "\n".join(lines)


def _automatic_said(limits: PolicyThresholds) -> str:
    """What the per-run rule gives, and the one thing that can still overrule it.

    **THIS SAID "SO NOBODY RELEASES THIS", WHICH IS AN OUTCOME AND NOT THE RULE.** The rule
    was right and the outcome was not: on 2026-08-06 a submitter was told automatic and
    their run parked at ``run-approval-lead``, because
    :func:`~edullm_platform.daily_ceiling.class_under_the_ceiling` raises an automatic
    submission to a lead once the runs released by nobody since midnight UTC have committed
    :attr:`~edullm_platform.contracts.policy.PolicyThresholds.automatic_daily_ceiling_usd`.

    ``check`` cannot see that and is not going to be taught to. The day is read off the run
    index, which is behind a credential the compile job holds and this verb deliberately
    does not -- reaching no network is what makes it free, local and instant, and a figure
    fetched here would be re-read at compile time anyway. So the clause says a lead may
    release it and declines to say whether one will.

    Silent when no ceiling is configured. That is the mechanism switched off rather than a
    day that could not be read, and the sentence above is exactly true without it.
    """
    rule = (
        "automatic by the per-run rule: one cell, under "
        f"${plain_decimal(limits.automatic_below_cost_usd)}."
    )
    ceiling = limits.automatic_daily_ceiling_usd
    if ceiling is None:
        return f"{rule} Nobody releases this."
    return (
        f"{rule} A team lead releases it instead once runs since midnight UTC have "
        f"committed the day's ${plain_decimal(ceiling)} automatic ceiling, and check "
        "reaches no network to know whether they have."
    )


def _why_not_automatic(
    preflight: Preflight,
    policy: ApprovalPolicy,
    inventory: OrganizationInventory,
    environment: ApprovalEnvironment,
) -> list[str]:
    """The sentence a routine run earns, which is always "here is what to change".

    There are two reasons a run reaches a lead under v5 and this names whichever holds. A
    submitter told the figure and the bound can see how far over they are; one told only the
    class cannot.

    The last line is the fallback and it is reachable, unlike the version of this that
    preceded v5. ``classify_request`` also holds back a digest whose registry scan findings
    carry no recorded review, and this verb cannot know that: it builds its facts with no
    scan policy, because the image digest it holds is a placeholder, and
    ``DEFERRED_TO_SUBMIT`` says so. So a run that reaches a lead for that reason reaches
    this line, and what it prints is who may release it rather than a reason this verb
    cannot stand behind.
    """
    cost = preflight.cost
    assert cost is not None  # only called with a priced submission
    limits = policy.thresholds
    reasons: list[str] = []
    if cost.cells > 1:
        reasons.append("a fan-out is never released automatically, whatever it costs")
    if cost.maximum_compute_cost_usd >= limits.automatic_below_cost_usd:
        reasons.append(
            f"over the automatic bound: ${plain_decimal(cost.maximum_compute_cost_usd)} is not "
            f"under ${plain_decimal(limits.automatic_below_cost_usd)}"
        )
    return reasons or [approvers_said(inventory, environment)]


def _deferred_block() -> str:
    lines = ["not checked here, because each of these needs the container registry"]
    for code, detail in DEFERRED_TO_SUBMIT:
        lines.append(f"  {code}")
        lines.extend(f"    {line}" for line in _wrap(detail, width=74))
    return "\n".join(lines)


def _workload_said(workload: WorkloadProfile) -> str:
    checkpoint = (
        f"checkpoint every {workload.checkpoint.interval_minutes}m"
        if workload.checkpoint is not None
        else "no checkpoint contract"
    )
    attempts = "attempt" if workload.maximum_attempts == 1 else "attempts"
    return _two_columns(
        workload.name,
        f"{plain_decimal(workload.maximum_runtime_hours)}h ceiling, "
        f"{workload.maximum_attempts} {attempts}, {checkpoint}",
    )


def _compute_said(compute: ComputeProfile) -> str:
    shape = CONTAINER_SHAPES.get(compute.name)
    devices = (
        f"{shape.gpus} GPU" if shape is not None and shape.gpus == 1 else None
    ) or (f"{shape.gpus} GPUs" if shape is not None and shape.gpus > 1 else f"{compute.accelerator}")
    return _two_columns(
        compute.name,
        f"{compute.instance_type}, {devices}, ${plain_decimal(compute.hourly_rate_usd)}/hour",
    )


def _dataset_said(preflight: Preflight) -> str:
    named = preflight.request.dataset_release
    reference = preflight.dataset
    if not named:
        return NOT_GIVEN
    if reference is None:
        return named
    return _two_columns(named, f"{reference.dataset_id} {reference.version}")


def _two_columns(first: str, second: str) -> str:
    """A name and a description on one line, the second column aligned where it can be."""
    if not second:
        return first
    return f"{first:<20} {second}" if len(first) < 20 else f"{first}  {second}"


def plain_decimal(value: Decimal) -> str:
    """The same rendering the approver page uses, for the same reason it uses it.

    ``StrictDecimal`` normalizes on the way in, so a reviewed ceiling of ``"500"`` is held
    as ``Decimal("5E+2")`` and interpolating it directly puts ``$5E+2`` in front of a
    reader.
    """
    return serialize_decimal(value)


def _wrap(text: str, width: int = 76) -> list[str]:
    """Wrapped at spaces and at nothing else, because these paragraphs carry names.

    ``textwrap`` breaks on hyphens by default, and almost everything this prints is
    hyphenated -- ``cancel-run.yml``, ``run-approval-lead``, ``gpu-4xa10g``, a dataset
    release, a filesystem path. Broken across two lines any of them stops being the string
    it names, and a reader copying it out gets something that does not exist.
    """
    from textwrap import wrap

    return wrap(text, width=width, break_on_hyphens=False, break_long_words=False) or [text]
