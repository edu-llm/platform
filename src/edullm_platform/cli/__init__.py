"""``edullm``, the binary a researcher drives instead of the Actions form.

A FACADE OVER THE SUBMISSION PATH THAT ALREADY EXISTS, AND NOTHING ELSE. Every governed
act still happens where it happened before: ``.github/workflows/submit-run.yml`` holds the
credential and dispatches admission, ``.github/workflows/cancel-run.yml`` holds the only
identity that may describe, tail or stop a Batch job. This package types the form for you
and reads the answer back. It talks to AWS nowhere, which is why it needs no credential of
its own and why ``gh`` is the whole of its authentication story.

WHAT IT ADDS IS THE REFUSAL THAT ARRIVES BEFORE THE QUEUE. Everything the reviewed
configuration can settle is settled locally, in the order the compile job settles it, by
importing the functions that own each rule rather than restating them --
``require_submitter_on_the_roster``, ``require_registered_repository``,
``require_a_process_for_every_device``, ``require_a_save_folder_a_retry_can_find``,
``build_request_facts``, ``classify_request``. A second spelling of any of those would
disagree with the server the first time only one of them was corrected, and the direction
it would fail is a submission the CLI cleared and admission refused, with a lead's
attention already spent on it.

THE VERB NAMES FOLLOW ``docs-frank/reference/decisions.md``, WHICH SETTLED THEM ON
2026-08-04: ``check`` is the validator and absorbs the scaffolding ``new`` used to do,
``submit``, ``status``, ``logs`` and ``cancel`` are unchanged. ``dry-run`` and ``new`` are
kept as aliases because every mockup and every guide written before that date spells them
that way, and a first-week researcher typing the spelling they were taught should get the
command rather than a usage error.
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """The console entry point, imported lazily so ``--help`` costs no configuration read."""
    from edullm_platform.cli.main import main as run

    return run(argv)
