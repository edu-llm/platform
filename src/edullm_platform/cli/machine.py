"""The document a machine reads, held apart from the paragraphs a person reads.

WHY THIS IS A SERIALIZER AND NOT A SECOND RENDERER. ``Preflight`` is a frozen dataclass of
pydantic contract models and ``render_preflight`` is a pure function of it; ``RunFacts``
stands the same way to ``render_run_facts``. Both verbs were structured-first with a human
renderer bolted on, which is the whole reason ``docs-frank/reference/designing-the-cli.md``
puts ``--json`` on these two and on no others. ``logs`` and ``cancel`` scan markdown headings
out of a workflow job log and there is no structure under that, so forcing a document onto
them would invent a shape rather than publish one.

**THE SHAPE IS NOT INVENTED HERE EITHER, AND THAT IS THE PART WORTH GUARDING.**
``tools/compile_submission.py`` already writes ``run_id``, ``submitter``, ``approval_class``,
``approving_environment``, ``manifest_sha256``, ``manifest`` and ``experiment`` into the
artifact ``status`` reads back. Those are the names, spelled the same way, so that a caller
holding a compiled submission and a caller holding a ``check`` are reading one vocabulary.
What is added is ``refusals``, ``deferred``, ``cost``, ``history`` and the envelope.

**THE ENVELOPE IS PINNED RATHER THAN DERIVED, AND TWO OTHER TOOLS ARE WHY.**
``docker ps --format json`` emits one object per line rather than an array, and the
maintainers closed the report because the shape had become load-bearing. The AWS CLI's
``--output text`` orders columns alphabetically by the underlying key, so a service adding a
field silently reorders somebody's script. A machine format derived from a structure but not
pinned to one is worse than none, so every document below carries ``format_version`` and a
field is added by editing this module rather than by adding an attribute somewhere upstream.

**AND ONE FIELD IS DELIBERATELY EMPTIED ON THE WAY OUT.** ``cli/preflight.py`` builds a real
``RunManifest`` with :data:`~edullm_platform.cli.preflight.UNRESOLVED_IMAGE_DIGEST` standing
in for the one field a laptop cannot fill, and its docstring promises that value is never
printed. It is a well-formed digest naming nothing, so a caller that read it could compare it
against a real one and be told they matched. ``image_digest`` therefore leaves here as
``None`` and ``manifest_sha256`` leaves as ``None`` with it, because a hash over a manifest
carrying a placeholder is not the hash the compile job will produce and publishing it would
be publishing a number that is wrong by construction.

MONEY IS BASE-TEN TEXT AND NEVER A FLOAT. ``presentation.py``'s header records what a second
arithmetic costs: a CLI that rounded differently from the approver page would have a
submitter and a lead reading two prices for one run. :func:`plain_decimal` is the same
rendering the approver page uses, and it is used here for the same reason.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Final, TextIO

from edullm_platform.checkpoint_commands import unverified_resume_note
from edullm_platform.cli.actions import RunFacts, SubmissionRun
from edullm_platform.cli.configuration import ReviewedConfiguration
from edullm_platform.cli.lane import placement_said
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT, Preflight, Refusal
from edullm_platform.cli.presentation import plain_decimal
from edullm_platform.cli.release import installed_version
from edullm_platform.placement import PlacementRecord
from edullm_platform.run_history import RUNGS

__all__ = [
    "FORMAT_VERSION",
    "check_document",
    "emit",
    "envelope",
    "refusal_document",
    "status_document",
    "status_listing_document",
]

#: The version of the envelope below, bumped when a field changes meaning or goes away.
#: Adding a field does not move it, which is the promise a reader needs: a caller reading
#: ``refusals`` today keeps reading it after a field is added beside it.
#:
#: 2 because ``exceeded`` went away with policy v5. It listed which routine ceiling a
#: request had crossed and there are no routine ceilings, so it could only ever be an empty
#: list, and a key that is always empty is one every caller keeps checking for nothing.
#: ``history`` arrived in the same document and would not have moved this on its own.
FORMAT_VERSION: Final = 2


def envelope(verb: str) -> dict[str, Any]:
    """The three keys every document carries, whatever the verb and whatever the outcome."""
    return {
        "format_version": FORMAT_VERSION,
        "edullm_version": installed_version().version,
        "verb": verb,
    }


def emit(document: dict[str, Any], *, out: TextIO) -> None:
    """One document, sorted and indented, exactly as the compile job writes its artifact.

    ``sort_keys=True`` and ``indent=2`` are copied from
    ``tools/compile_submission.py``'s own write rather than chosen again, so a caller holding
    both files is reading one formatting. Sorted keys also make the output byte-stable across
    a Python whose dict ordering changed, which is what lets a test compare two runs.

    **``ensure_ascii`` IS WRITTEN OUT BECAUSE THE DEFAULT IS LOAD-BEARING HERE AND READS
    LIKE NOISE.** It escapes every character above ASCII to ``\\uXXXX``, so a document
    naming a checkpoint with an accent in it, or carrying an arrow in a refusal detail, is
    pure ASCII by the time it reaches :func:`print`. That is what kept ``--json`` out of the
    crash that
    ``cli.__init__`` fixes for the prose: an ASCII document encodes under cp1252, cp932 and
    every other code page, so the machine-readable half never depended on the stream being
    UTF-8 and still does not. Somebody tidying this line would find ``ensure_ascii=False``
    reads better and produces smaller output, and would be putting the worse half of the bug
    back -- a script parsing this gets a truncated document and an exit code that says
    nothing, where a person at least gets a traceback to read.

    It is worth keeping now that the stream is UTF-8, because the document does not stop
    being handled when this process writes it. Whatever captures a redirect may re-encode it
    on the way to a file, and an ASCII document is the same bytes under every encoding that
    contains ASCII. ``json.loads`` restores the characters, so no caller loses anything.
    """
    print(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True), file=out)


def refusal_document(verb: str, refusals: Sequence[Refusal]) -> dict[str, Any]:
    """A refusal reported as a document rather than as a paragraph, for the verbs that can.

    Used where a verb refuses before it has anything else to say: a malformed run id, an
    ambiguous one, an id the window does not reach. The envelope and ``refusals`` are the
    whole of it, because there is nothing else that was established.
    """
    return {
        **envelope(verb),
        "refused": True,
        "refusals": [{"code": refusal.code, "detail": refusal.detail} for refusal in refusals],
    }


def check_document(
    preflight: Preflight,
    *,
    configuration: ReviewedConfiguration,
    submitter: str | None,
    placement: PlacementRecord | None = None,
) -> dict[str, Any]:
    """Everything ``edullm check`` established, in the compile job's own vocabulary.

    ``run_id`` and ``manifest_sha256`` are always ``None`` here and are present anyway. A
    caller written against a compiled submission and a caller written against a check are
    reading the same key set, which is what lets one skill handle both, and a key that is
    absent on one path is a key every caller has to guard.

    ``placement`` is passed in rather than read here, because the verb has already opened
    ``config/capacity.yaml`` to print the sentence and two reads would describe two moments.
    """
    return {
        **envelope("check"),
        "config_directory": str(configuration.directory),
        "submitter": submitter,
        "refused": preflight.refused,
        "refusals": [
            {"code": refusal.code, "detail": refusal.detail} for refusal in preflight.refusals
        ],
        "deferred": [{"code": code, "detail": detail} for code, detail in DEFERRED_TO_SUBMIT],
        "run_id": None,
        "manifest_sha256": None,
        "manifest": _manifest_of(preflight),
        "experiment": preflight.request.experiment or None,
        "team": preflight.request.team or None,
        "team_source": preflight.team_source or None,
        "approval_class": (
            None if preflight.approval_class is None else preflight.approval_class.value
        ),
        "approving_environment": (
            None
            if preflight.approving_environment is None
            else preflight.approving_environment.value
        ),
        "cost": _cost_of(preflight),
        "retries": _retries_of(preflight),
        "history": _history_of(preflight),
        "placement": _placement_of(placement),
    }


def _retries_of(preflight: Preflight) -> dict[str, Any] | None:
    """What the attempt factor in ``cost`` buys, for a request asking for more than one.

    ``None`` for a single-attempt run, the way ``placement`` is ``None`` for a shape that
    places promptly: there is no second attempt to describe, and a key that appeared on
    every check carrying nothing is a key every caller reads past.

    Branch on ``maximum_attempts`` and ``resume_required`` and print ``said``, which is the
    split ``_history_of`` and ``_placement_of`` already make. The two numbers are reviewed
    configuration and the sentence is prose this repository rewords.

    ``resume_required`` is the workload profile's declaration and not a finding. Nothing on
    this platform verifies it against the codebase that would have to honour it, which is
    what ``said`` exists to state in words -- a caller that reads ``true`` here and concludes
    a retry resumes has made exactly the inference this field cannot support.
    """
    manifest = preflight.manifest
    if manifest is None:
        return None
    said = unverified_resume_note(
        maximum_attempts=manifest.maximum_attempts,
        workload_profile=manifest.workload_profile,
        checkpoint=manifest.checkpoint,
    )
    if said is None:
        return None
    return {
        "maximum_attempts": manifest.maximum_attempts,
        "resume_required": (
            None if manifest.checkpoint is None else manifest.checkpoint.resume_required
        ),
        "said": said,
    }


def _placement_of(verdict: PlacementRecord | None) -> dict[str, Any] | None:
    """Whether this account has been able to get the shape, as a verdict and as the sentence.

    ``None`` for a shape ``config/capacity.yaml`` records as placing reliably, and for a check
    that never resolved a compute profile at all. Both are "there is nothing to say", and a key
    that disappears would be one every caller has to guard.

    ``places`` is the field to branch on and ``said`` is the one to print. That split is
    ``_history_of``'s and the reason is the same: the verdict is a value from a closed set that
    a reviewed file fixes, and the sentence is prose this repository rewords -- it has already
    been rewritten once, when the third verdict arrived and ``after_a_wait`` stopped being told
    it might never place.
    """
    if verdict is None or (said := placement_said(verdict)) is None:
        return None
    return {
        "profile": verdict.profile,
        "places": verdict.places,
        "measured_by": verdict.measured_by,
        "wait": verdict.wait,
        "said": said,
    }


def _history_of(preflight: Preflight) -> dict[str, Any] | None:
    """What runs of this shape have taken, as counts rather than as the sentence.

    ``said`` is carried too, because a caller printing one line to a person should print the
    same line this tool prints rather than composing a second one from the counts. What a
    caller must not do is branch on it: the counts are the structure and the sentence is
    prose that will be reworded.

    ``cohort`` is ``None`` when there is no reading packaged and when nothing of this shape
    has ever succeeded, and ``said`` is what tells those apart. Both are honest answers and
    neither is a number.

    ``measured_at`` is the one field a caller has to read before it trusts the rest. The
    digest is committed and travels with the install, so an old install quotes an old
    reading and has no way to know it is old. A caller deciding whether to believe a median
    branches on this; one printing a line to a person can print ``said``, which carries the
    same date in words.
    """
    answer = preflight.history
    if answer is None:
        return None
    cohort = answer.cohort
    return {
        "said": answer.said,
        "measured_at": None if answer.measured_at is None else answer.measured_at.isoformat(),
        "matched_on": None if cohort is None else list(RUNGS[cohort.rung][0]),
        "succeeded": None if cohort is None else cohort.succeeded,
        "failed": None if cohort is None else cohort.failed,
        "fastest_seconds": None if cohort is None else _seconds(cohort.fastest_seconds),
        "median_seconds": None if cohort is None else _seconds(cohort.median_seconds),
        "slowest_seconds": None if cohort is None else _seconds(cohort.slowest_seconds),
    }


def _seconds(value: Decimal | None) -> str | None:
    """Text and never a JSON number, for the reason money is text two functions down."""
    return None if value is None else str(value)


def _manifest_of(preflight: Preflight) -> dict[str, Any] | None:
    """The manifest as the compile job would serialize it, minus the field it cannot know.

    Emptied rather than omitted. A caller reading ``manifest["image_digest"]`` gets ``None``
    and knows the answer is unresolved; a caller reading a key that is not there gets a
    ``KeyError`` on the one path where the manifest is otherwise complete.
    """
    if preflight.manifest is None:
        return None
    document: dict[str, Any] = preflight.manifest.model_dump(mode="json")
    document["image_digest"] = None
    return document


def _cost_of(preflight: Preflight) -> dict[str, Any] | None:
    """The five factors and their product, as the worst case block prints them."""
    cost = preflight.cost
    if cost is None:
        return None
    return {
        "hourly_rate_usd": plain_decimal(cost.hourly_rate_usd),
        "nodes": cost.nodes,
        "maximum_runtime_hours": plain_decimal(cost.maximum_runtime_hours),
        "maximum_attempts": cost.maximum_attempts,
        "cells": cost.cells,
        "maximum_compute_cost_usd": plain_decimal(cost.maximum_compute_cost_usd),
    }


def status_document(facts: RunFacts) -> dict[str, Any]:
    """One run, as far as GitHub can answer, and whether the rest costs a runner.

    ``aws_report`` is present and always ``None``. It is the place a caller would look for
    what ``cancel-run.yml`` reports, and the answer is that this verb does not dispatch under
    ``--json``: the report is markdown scraped out of a job log, so a field carrying it would
    publish a shape that does not exist. The key is here rather than absent so that the
    answer is "nothing was asked" rather than a ``KeyError``.

    ``declined`` is present and ``None`` for every run nobody said no to, for the same
    reason. A caller branching on whether a run failed has to be able to ask whether it was
    declined instead, and reading that out of the prose in ``because`` is exactly what the
    machine-readable form exists to avoid.
    """
    declined = facts.declined
    return {
        **envelope("status"),
        "run_id": facts.run_id,
        "admitted": facts.admitted.value,
        "because": facts.because,
        "needs_a_dispatch": facts.needs_a_dispatch,
        "was_found": facts.was_found,
        "submission": None if facts.submission is None else _submission_of(facts.submission),
        "gate": facts.gate,
        "reviewers": list(facts.reviewers),
        "you_can_release": facts.you_can_release,
        "approver": facts.approver,
        "approved_at": None if facts.approved_at is None else facts.approved_at.isoformat(),
        "declined": None
        if declined is None
        else {
            "by": declined.by,
            "reason": declined.reason,
            "at": None if declined.at is None else declined.at.isoformat(),
        },
        "experiment": facts.experiment,
        "team": facts.team,
        "aws_report": None,
        "refused": False,
        "refusals": [],
    }


def status_listing_document(runs: Sequence[SubmissionRun]) -> dict[str, Any]:
    """The recent submissions, under a key rather than as a bare array.

    A top-level array has nowhere to carry ``format_version``, so the day a field changes
    meaning there is no way to say so. ``docker ps --format json`` is the worked example of
    what that costs once callers exist.
    """
    return {
        **envelope("status"),
        "runs": [_submission_of(run) for run in runs],
        "refused": False,
        "refusals": [],
    }


def _submission_of(run: SubmissionRun) -> dict[str, Any]:
    """One dispatch of ``submit-run.yml``, including the short form the listing prints.

    ``short_run_id`` is emitted beside ``run_id`` rather than left to the caller to slice.
    Its length is measured rather than chosen and the measurement is in that property's own
    docstring, so a caller computing it independently would be recomputing a decision and
    would get it wrong the day the measurement changes.
    """
    return {
        "workflow_run_id": run.workflow_run_id,
        "run_id": run.run_id,
        "short_run_id": run.short_run_id,
        "state": run.state,
        "created_at": run.created_at.isoformat(),
        "url": run.url,
        "experiment": run.experiment,
        "cells": run.cells,
    }
