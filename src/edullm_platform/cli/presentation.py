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
is what keeps it that way. Every bound, rate and ceiling that reaches a terminal is
interpolated out of the loaded configuration at the moment of printing, so the only way to
change what ``edullm`` says a limit is is to change the file that is the limit. The rule is
structural rather than a habit because the runtime bound has already disagreed between the
documents and the configuration three separate times, and each of those was two copies that
agreed on the day somebody wrote the second one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from edullm_platform.cli.actions import RunFacts, elapsed_said
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT, Preflight, Refusal
from edullm_platform.contracts.base import serialize_decimal
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy
from edullm_platform.contracts.workload import ComputeProfile, WorkloadProfile
from edullm_platform.execution import CONTAINER_SHAPES

__all__ = [
    "plain_decimal",
    "render_preflight",
    "render_refusals",
    "render_run_facts",
    "render_run_listing",
]

#: Where the second column starts. Wide enough for ``experiment`` and the longest label
#: below it, and narrow enough that a value fits beside it in eighty columns.
LABEL_WIDTH = 18


def render_preflight(preflight: Preflight, *, policy: ApprovalPolicy) -> str:
    """The whole of what ``edullm check`` prints, refused or not."""
    if preflight.refused:
        return render_refusals(preflight.refusals)
    blocks = [
        _manifest_block(preflight),
        _cost_block(preflight),
        _approval_block(preflight, policy),
        _deferred_block(),
        "no refusals. edullm submit will dispatch this.",
    ]
    return "\n\n".join(block for block in blocks if block) + "\n"


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
            "no runs. Nothing you have submitted is still known to GitHub Actions, which "
            "keeps workflow runs for a bounded window.\n"
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
        "worst case\n"
        f"  ${plain_decimal(cost.hourly_rate_usd)}/hour x {cost.nodes} {nodes} x "
        f"{plain_decimal(cost.maximum_runtime_hours)}h x {cost.maximum_attempts} {attempts} x "
        f"{cost.cells} {cells} = ${plain_decimal(cost.maximum_compute_cost_usd)}\n"
        "  This is the ceiling, not an estimate. It is also what routes the run, so "
        "lowering\n"
        "  --hours is what moves a short run under the automatic bound."
    )


def _approval_block(preflight: Preflight, policy: ApprovalPolicy) -> str:
    approval_class = preflight.approval_class
    cost = preflight.cost
    if approval_class is None or cost is None or preflight.approving_environment is None:
        return ""
    limits = policy.thresholds
    lines = ["approval"]
    if approval_class is ApprovalClass.AUTOMATIC:
        lines.append(
            f"  automatic -- under ${plain_decimal(limits.automatic_below_cost_usd)} and under "
            f"{plain_decimal(limits.automatic_below_runtime_hours)}h. Nobody releases this."
        )
        return "\n".join(lines)

    lines.append(f"  {approval_class.value} -> {preflight.approving_environment.value}")
    if approval_class is ApprovalClass.EXCEPTION:
        # Every reason comes from ``exceeded_routine_bounds``, including the rate, which
        # this block used to word for itself because that function reported four bounds and
        # not the fifth. It reports five now and ``run_preflight`` hands it the rate, so a
        # sentence composed here would be a second spelling of one an approver reads on the
        # page this verb is previewing.
        lines.extend(f"  {reason}" for reason in preflight.exceeded)
        return "\n".join(lines)

    lines.extend(f"  {reason}" for reason in _why_not_automatic(preflight, policy))
    return "\n".join(lines)


def _why_not_automatic(preflight: Preflight, policy: ApprovalPolicy) -> list[str]:
    """The sentence a routine run earns, which is always "here is what to change".

    Sixty-seven cents is the case this exists for. ``gpu-4xa10g``'s cheapest possible
    submission lands just over the automatic bound, so every submission on it costs a
    lead's attention -- and a submitter who is told the figure and the bound can see that
    at a glance where one told only the class cannot.
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
    if cost.maximum_runtime_hours >= limits.automatic_below_runtime_hours:
        reasons.append(
            f"over the automatic bound: {plain_decimal(cost.maximum_runtime_hours)}h is not under "
            f"{plain_decimal(limits.automatic_below_runtime_hours)}h"
        )
    return reasons or ["any of the nine approvers can release it"]


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
