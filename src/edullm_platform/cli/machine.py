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
What is added is ``refusals``, ``deferred``, ``cost`` and the envelope.

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
from typing import Any, Final, TextIO

from edullm_platform.cli.configuration import ReviewedConfiguration
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT, Preflight, Refusal
from edullm_platform.cli.presentation import plain_decimal
from edullm_platform.cli.release import installed_version

__all__ = [
    "FORMAT_VERSION",
    "check_document",
    "emit",
    "envelope",
    "refusal_document",
]

#: The version of the envelope below, bumped when a field changes meaning or goes away.
#: Adding a field does not move it, which is the promise a reader needs: a caller reading
#: ``refusals`` today keeps reading it after a field is added beside it.
FORMAT_VERSION: Final = 1


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
    """
    print(json.dumps(document, indent=2, sort_keys=True), file=out)


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
) -> dict[str, Any]:
    """Everything ``edullm check`` established, in the compile job's own vocabulary.

    ``run_id`` and ``manifest_sha256`` are always ``None`` here and are present anyway. A
    caller written against a compiled submission and a caller written against a check are
    reading the same key set, which is what lets one skill handle both, and a key that is
    absent on one path is a key every caller has to guard.
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
        "exceeded": list(preflight.exceeded),
        "cost": _cost_of(preflight),
    }


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
