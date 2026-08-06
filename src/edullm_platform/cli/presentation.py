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
the one reader placed to notice it had drifted.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path

from edullm_platform.cli.actions import RunFacts, elapsed_said
from edullm_platform.cli.configuration import PACKAGED_CONFIG_DIRECTORY, ReviewedConfiguration
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT, Preflight, Refusal
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.authorization import (
    holds_exception_approver_role,
    holds_routine_approver_role,
)
from edullm_platform.contracts.base import serialize_decimal
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy
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
    if preflight.refused:
        return f"{source}\n\n{render_refusals(preflight.refusals)}"
    blocks = [
        _manifest_block(preflight),
        _cost_block(preflight),
        _history_block(preflight),
        _approval_block(preflight, configuration.policy, configuration.inventory),
        _deferred_block(),
        "no refusals. edullm submit will dispatch this.",
    ]
    return f"{source}\n\n" + "\n\n".join(block for block in blocks if block) + "\n"


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


def render_refusals(refusals: Sequence[Refusal]) -> str:
    """The refusal form: how many, that nothing moved, then one block each.

    "Nothing was dispatched" is on the first line rather than the last, because a reader
    seeing a wall of red needs to know the run did not start before they need to know why.
    """
    count = len(refusals)
    noun = "refusal" if count == 1 else "refusals"
    lines = [f"{count} {noun}. Nothing was dispatched.", ""]
    for refusal in refusals:
        lines.append(f"refused  {refusal.code}")
        lines.extend(f"  {line}" for line in _wrap(refusal.detail))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


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
    return "\n".join(lines) + "\n"


def _manifest_block(preflight: Preflight) -> str:
    request = preflight.request
    rows: list[tuple[str, str]] = [
        ("repository", request.repository),
        # Twelve characters, which is what the image tag carries and what every transcript
        # and every approver page prints. The full forty go to the workflow.
        ("commit", request.commit_sha[:12]),
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
    rows.append(("team", _two_columns(request.team, preflight.team_source)))
    rows.append(("experiment", request.experiment))
    rows.append(("wandb project", request.wandb_project))
    return "manifest\n" + "\n".join(f"  {label:<{LABEL_WIDTH}}{value}" for label, value in rows)


def _cost_block(preflight: Preflight) -> str:
    cost = preflight.cost
    if cost is None:
        return ""
    cells = "cell" if cost.cells == 1 else "cells"
    attempts = "attempt" if cost.maximum_attempts == 1 else "attempts"
    nodes = "node" if cost.nodes == 1 else "nodes"
    return (
        f"worst case  ${plain_decimal(cost.maximum_compute_cost_usd)}\n"
        f"  ${plain_decimal(cost.hourly_rate_usd)}/hour x {cost.nodes} {nodes} x "
        f"{plain_decimal(cost.maximum_runtime_hours)}h x {cost.maximum_attempts} {attempts} x "
        f"{cost.cells} {cells}\n"
        "  A ceiling rather than an estimate, and what routes the run. Lowering --hours\n"
        "  is what moves a run under the automatic bound."
    )


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
    if approval_class is None or cost is None or preflight.approving_environment is None:
        return ""
    limits = policy.thresholds
    lines = ["approval"]
    if approval_class is ApprovalClass.AUTOMATIC:
        lines.append(
            f"  automatic. One cell, under ${plain_decimal(limits.automatic_below_cost_usd)}, "
            "so nobody releases this."
        )
        return "\n".join(lines)

    lines.append(f"  {approval_class.value} -> {preflight.approving_environment.value}")
    lines.extend(
        f"  {reason}"
        for reason in _why_not_automatic(
            preflight, policy, inventory, preflight.approving_environment
        )
    )
    return "\n".join(lines)


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
    lines = ["not checked here, because both need the container registry"]
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
