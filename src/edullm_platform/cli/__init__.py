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
``submit``, ``status``, ``logs`` and ``cancel`` are unchanged, ``status`` with no run id
absorbs ``activity``, and ``notebook`` is a flag on ``shell``.

THE RETIRED SPELLINGS ARE REFUSED RATHER THAN ALIASED, AND THE REFUSAL NAMES THE
REPLACEMENT. Every mockup and every guide written before that date types ``dry-run`` and
``new``, so the tempting move is to accept both -- but an alias makes two names work and
teaches nobody which is the name, and the retired one then reappears in the next guide
somebody writes. Fewer names is the whole direction of this design, so the old spelling
costs one retry and ends there, and what it buys for that retry is a sentence naming what
``check`` would do in the repository the person is standing in.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

__all__ = ["main"]


def _speak_utf8(stream: TextIO | None) -> None:
    """Change one stream's codec to UTF-8, in place, before anything has written to it.

    **THE PROBLEM IS A PIPE AND NOT A TERMINAL.** Windows has given the console itself
    UTF-16 since PEP 528, so ``edullm logs`` on screen is fine and stays fine. Redirect it
    and ``sys.stdout`` is an ordinary file wrapper whose encoding is
    ``locale.getpreferredencoding()``, which on Windows is the ANSI code page. On the
    machines this platform's researchers use that is cp1252, and cp1252 has no slot for a
    great deal of what a training log prints. Measured on 2026-08-05 against a cp1252 stream:
    ``logs`` on a run whose tail held ``step 200 -> step 400`` written with U+2192 raised
    UnicodeEncodeError, ``log.txt`` was left **empty** rather than truncated -- the report is
    one ``print`` and the encode fails before a byte of it is written -- and the traceback
    went to the terminal. So the researcher gets no log and a stack trace, and the traceback
    is the thing this package's interrupt handler says in its own comment must never reach a
    researcher. An accented model name survives, as it happens, because cp1252 carries
    Latin-1; what kills it is the arrows, the box drawing and the progress bars, which is
    most of what a tail is. Python 3.12 through 3.14; PEP 686 turns UTF-8 mode on by default
    in 3.15 and closes it, which is another reason not to leave it to the interpreter.

    **THIS IS NOT THE SUBPROCESS BUG AND FIXING EITHER ONE LEAVES THE OTHER.** That one is
    ``gh`` handing us UTF-8 bytes that ``text=True`` decodes as cp1252, inbound, and it is
    answered where ``SubprocessRunner`` names its encoding. This one is our own strings
    going out through a codec that cannot spell them. Opposite directions, two streams, no
    overlap.

    ``reconfigure`` rather than a second ``TextIOWrapper`` over ``stream.buffer``, because
    argparse writes ``--help`` and every usage error straight to ``sys.stdout`` and
    ``sys.stderr`` and never sees a wrapper we made. Two wrappers on one descriptor is two
    buffers, and then what a researcher reads depends on which one flushed first.
    Reconfiguring changes the codec of the one stream that everything already writes
    through, the traceback printer included.

    ``backslashreplace`` is not what makes a training log printable -- UTF-8 has a spelling
    for every character, so the codec alone does that. It covers the one string UTF-8 still
    refuses under ``strict``: a lone surrogate, which ``os.fsdecode`` puts in a path when
    Windows hands back UTF-16 that is not valid. This function exists because printing
    raised, so it must not leave a way for printing to raise.
    """
    # None under pythonw.exe, which has no streams at all, and something without a codec to
    # change if a caller has already put its own object on sys.stdout.
    if stream is None or not hasattr(stream, "reconfigure"):
        return
    stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    """The console entry point, imported lazily so ``--help`` costs no configuration read."""
    # **THE BOUNDARY IS HERE BECAUSE THIS FUNCTION IS THE ONLY PLACE THE PROCESS'S STREAMS
    # ARE OURS.** ``pyproject.toml`` names this as the console script, so arriving here means
    # this process is ``edullm`` and is nothing else; there is no other caller to surprise.
    # One step in, ``cli.main.main`` takes ``out`` and ``err`` as arguments and the suite
    # hands it StringIO -- doing it there would either reconfigure a caller's stream or, in
    # the suite, be skipped entirely and leave the tests measuring a stream no researcher
    # has. At import it would reach every consumer of the package, pytest's capture
    # included, which is the same fault one layer worse.
    #
    # PYTHONIOENCODING IS LEFT ALONE, because it is the documented way to say which encoding
    # you want and a program that overrules a stated intent has traded one bug for another.
    # Nothing else here names an encoding deliberately: what the fix is for is the encoding
    # nobody chose, that the interpreter read off the locale.
    if not os.environ.get("PYTHONIOENCODING"):
        _speak_utf8(sys.stdout)
        _speak_utf8(sys.stderr)

    from edullm_platform.cli.main import main as run

    return run(argv)
