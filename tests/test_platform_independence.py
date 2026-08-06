"""Three defaults that mean different things on different machines, and are named here instead.

**ALL THREE ARE CORRECT BY ACCIDENT ON THE MACHINE EVERY CONTRIBUTOR USES.** A test that ran the
code and compared the answer would pass on macOS and on the Ubuntu runner whichever way the code
went, which is why the first two are not asserted by running them. What is asserted is that the
argument deciding the behaviour is written down, because writing it down is the whole fix. The
third is different and can be run here, by handing the code the stream Windows would have handed
it; the cases say so where they do it.

``subprocess.run(text=True)`` with no ``encoding`` decodes with the locale's codec. That is
UTF-8 on macOS and Linux and the ANSI code page on Windows, usually cp1252, against a ``gh``
that emits UTF-8 -- and cp1252 has undefined bytes, so a log line with an accented character in
it can raise ``UnicodeDecodeError`` out of the call rather than merely mangle. Python 3.15
turns UTF-8 mode on by default (PEP 686), which changes it a third time, and
``uv tool install`` fetches the newest interpreter where no suitable one exists. The 3.15 note
says in as many words that an explicit ``encoding`` should always be given for compatibility
between versions.

``Path.write_text`` with no ``newline`` translates every ``\\n`` to ``os.linesep``, so the run
spec this tool scaffolds is CRLF on Windows and LF everywhere else. It is committed into a
research repository, which is not guaranteed to carry a ``.gitattributes``; this repository's
own records the incident it was written for, a 32-line change that arrived as 1,795 lines.
Nothing fails, which is the problem -- ``check`` reads with ``newline=None`` and folds it back,
so a Windows researcher produces a file that differs from everybody else's before they have
typed anything.

``sys.stdout`` is UTF-16 on a Windows console and the ANSI code page on a Windows pipe, so
``edullm logs`` is fine on screen and raises ``UnicodeEncodeError`` the moment it is redirected
into a file. That is the opposite direction from the ``subprocess`` fault above and shares no
code with it: one is what we decode coming in from ``gh``, the other is what we encode going
out. The suite could not see this one at all before these cases, because ``cli_support.invoke``
hands the CLI a ``StringIO``, which holds any character there is and never raises.
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Final

import pytest

import edullm_platform.cli
from edullm_platform.cli.configuration import load_reviewed_configuration
from edullm_platform.cli.machine import emit
from edullm_platform.cli.scaffold import scaffold_spec
from tests.cli_support import CONFIG_DIR

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

#: The code a researcher installs, which is the population this is about. ``tools/`` runs on
#: the Ubuntu runner and on the owner's laptop and never on a researcher's Windows machine,
#: so it is out -- named here rather than left to a glob, so that widening it is a decision.
SHIPPED: Final = (PROJECT_ROOT / "src" / "edullm_platform", PROJECT_ROOT / "client" / "src")

#: A registered repository, so the scaffold has a workload to resolve and writes a real spec.
REPOSITORY: Final = "OLMo-core"

#: The one shipped module whose ``subprocess.run`` is left with the locale's codec, named here
#: rather than pattern-matched so that a second one is a decision somebody makes in this file.
#:
#: ``contracts.source_identity._run_git`` is reached only through ``verify_source_identity``,
#: whose only caller is ``tools/verify_source_identity.py``, which runs in two steps of
#: ``build-research-image.yml`` on ``ubuntu-latest``. No researcher's machine executes it, so
#: the locale there is UTF-8 today and PEP 686 keeps it UTF-8 under Python 3.15: naming the
#: codec would change no byte anywhere. What it would change is the admission validator's zip,
#: because ``contracts.image`` imports ``SourceIdentity`` and the packager follows it -- so the
#: deployed Lambda would owe a release, and somebody would have to open a ``PendingRelease``
#: and then clear it, for a decode that cannot differ. The module is packaged and the function
#: is unreachable inside it: a Lambda has no git.
#:
#: If this module ever gains a ``subprocess.run`` the CLI can reach, this exclusion is wrong
#: and should be deleted rather than widened.
DECODES_ONLY_ON_A_RUNNER: Final = "src/edullm_platform/contracts/source_identity.py"


def subprocess_calls(tree: ast.Module) -> list[ast.Call]:
    """Every ``subprocess.run`` in one module, as the call node."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]


def keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((word.value for word in call.keywords if word.arg == name), None)


def shipped_modules() -> list[Path]:
    return sorted(path for root in SHIPPED for path in root.rglob("*.py"))


def calls_that_decode() -> list[tuple[str, int, ast.Call]]:
    """Every shipped ``subprocess.run`` a researcher's machine can reach, with where it is."""
    found: list[tuple[str, int, ast.Call]] = []
    for path in shipped_modules():
        where = path.relative_to(PROJECT_ROOT).as_posix()
        if where == DECODES_ONLY_ON_A_RUNNER:
            continue
        for call in subprocess_calls(ast.parse(path.read_text(encoding="utf-8"))):
            found.append((where, call.lineno, call))
    return found


def test_the_sweep_finds_the_calls_this_package_makes() -> None:
    """The enumeration reached the shipped code, or the assertion below proved nothing.

    Three on 2026-08-05, out of four in the shipped tree: the one in ``SubprocessRunner``
    that drives git and gh, and the two that drive the AWS CLI. The fourth is excluded by
    name and :func:`test_the_exclusion_is_one_module_and_it_still_exists` holds it to being
    real. Asserted as a floor rather than as three, because a fourth is something somebody
    should be able to write without editing a test -- and it will be held to the rule below
    when they do.
    """
    found = calls_that_decode()
    assert len(found) >= 3
    assert any("cli/workspace.py" in where for where, _, _ in found)


def test_the_exclusion_is_one_module_and_it_still_exists() -> None:
    """An exclusion that stopped naming anything would silently widen the sweep's blind spot.

    Mutation: leave the constant pointing at a module somebody has since renamed. The sweep
    would go on passing and the reason written beside the name would be describing nothing.
    """
    excluded = PROJECT_ROOT / DECODES_ONLY_ON_A_RUNNER
    assert excluded.is_file()
    assert subprocess_calls(ast.parse(excluded.read_text(encoding="utf-8")))


def test_every_shipped_subprocess_names_the_codec_it_decodes_with() -> None:
    """Mutation: leave ``text=True`` to mean whatever the locale means.

    It is right on every machine anybody here develops on, and wrong on the one machine the
    person who cannot install this tool is using.
    """
    silent = [
        f"{where}:{line}"
        for where, line, call in calls_that_decode()
        if keyword(call, "text") is not None and keyword(call, "encoding") is None
    ]
    assert not silent, (
        "these decode a subprocess with the locale's codec, which is cp1252 on Windows "
        f"against tools that emit UTF-8: {', '.join(silent)}"
    )


def test_the_codec_named_is_utf_8_and_a_bad_byte_does_not_kill_the_verb() -> None:
    """``errors`` as well as ``encoding``, because strict is a dead verb rather than a mangle.

    Mutation: name the encoding and leave the error handler at strict. ``edullm logs`` prints
    workflow job log lines, and a run whose container printed one accented character would
    then raise out of ``subprocess.run`` instead of printing a slightly wrong line.
    """
    for where, line, call in calls_that_decode():
        encoding = keyword(call, "encoding")
        assert isinstance(encoding, ast.Constant) and encoding.value == "utf-8", (
            f"{where}:{line} names an encoding this suite cannot read as utf-8"
        )
        errors = keyword(call, "errors")
        assert isinstance(errors, ast.Constant) and errors.value == "replace", (
            f"{where}:{line} decodes strictly, so one undecodable byte from a subprocess "
            "raises rather than mangling"
        )


def test_the_scaffold_writes_lf_whatever_it_is_running_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: write the spec in text mode and let the platform choose the line ending.

    Asserted at the call rather than over the bytes, and that is the whole point of the case:
    over the bytes it passes on macOS either way, because ``os.linesep`` here is already what
    the argument asks for. The one machine where the two differ is the one no test in this
    suite runs on.
    """
    written: dict[str, Any] = {}
    real = Path.write_text

    def record(self: Path, data: str, **rest: Any) -> int:
        written.update(rest)
        written["path"] = self
        return real(self, data, **rest)

    monkeypatch.setattr(Path, "write_text", record)

    path = scaffold_spec(
        load_reviewed_configuration(CONFIG_DIR), repository=REPOSITORY, root=tmp_path
    )

    assert written["path"] == path
    assert written.get("newline") == "\n", (
        "the run spec is written in text mode with no newline argument, so it gets CRLF on "
        "Windows and LF everywhere else, and it is committed into a research repository"
    )
    assert written.get("encoding") == "utf-8"
    assert b"\r\n" not in path.read_bytes()


# ---------------------------------------------------------------------------------------
# the stream this program prints on
# ---------------------------------------------------------------------------------------


#: What a training log holds that an ANSI code page does not. The accented name is in the list
#: on purpose and cp1252 survives it -- cp1252 carries Latin-1 -- so a case that used only an
#: accent would pass on Windows for the wrong reason and prove nothing. The arrow and the block
#: are what actually kill a redirect, and they are in every progress bar OLMo-core prints.
BEYOND_THE_CODE_PAGE: Final = "step 200 \u2192 400  train \u2588\u2588\u2588\u2591\u2591 62%  ren\u00e9e"


def a_windows_pipe() -> io.TextIOWrapper:
    """The stream Python hands a program on Windows when stdout is redirected, not a console.

    Built here rather than mocked, because the fault is entirely in the codec: this is a real
    ``TextIOWrapper`` with the encoding a Windows ``locale.getpreferredencoding()`` returns,
    and it raises for exactly the characters the real one raises for. PEP 528 gave the console
    UTF-16 and left this case alone, which is why the bug only appears under a redirect.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252")


def test_the_stream_windows_supplies_cannot_carry_a_training_log() -> None:
    """The fault itself, so that everything below is answering something real.

    Mutation: none available -- this asserts the platform rather than our code. It is here
    because the three cases after it are worth nothing if cp1252 turns out to be able to hold
    what ``logs`` prints, and because the suite's own ``StringIO`` can hold anything and so
    quietly disagrees with every researcher's stdout.
    """
    with pytest.raises(UnicodeEncodeError):
        print(BEYOND_THE_CODE_PAGE, file=a_windows_pipe())

    # And the half that makes the bug hard to see from here: the accent alone gets through, so
    # a Windows researcher hits this on a progress bar rather than on a model name.
    print("ren\u00e9e", file=a_windows_pipe())


def test_importing_the_package_leaves_the_process_streams_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: reconfigure at import time, which is the obvious place and the wrong one.

    Every consumer of the package inherits an import, this suite included: pytest's capture
    would be reconfigured out from under it and the bytes these cases compare would stop being
    the bytes anything else produces. The entry point is a narrower place to put it and
    :func:`test_the_console_entry_point_makes_the_streams_carry_utf_8` holds it there.

    **A cp1252 STREAM IS PUT IN PLACE FIRST, AND WITHOUT IT THIS CASE ASSERTS NOTHING.** The
    stdout pytest supplies is already UTF-8, so an import-time reconfigure would change it to
    what it already was and pass. Measured: the mutation survived this case until the stream
    below was installed, and was caught only by the two after it.
    """
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    pipe = a_windows_pipe()
    monkeypatch.setattr(sys, "stdout", pipe)
    monkeypatch.setattr(sys, "stderr", a_windows_pipe())

    importlib.reload(edullm_platform.cli)

    assert sys.stdout is pipe
    assert sys.stdout.encoding == "cp1252", (
        "importing the package changed the encoding of a stream it was only imported into, "
        "which reaches every consumer of it including this suite's own capture"
    )


def test_the_console_entry_point_makes_the_streams_carry_utf_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: drop the two calls, or make only ``stdout`` of them.

    ``stderr`` matters as much as ``stdout`` and for a worse reason: it carries every refusal,
    and it is where Python prints a traceback. A traceback raised while printing a log line,
    landing on a stream that cannot print it either, is a crash with nothing on the screen.

    ``argv=[]`` because the orientation text costs no configuration read and no network, and
    what is being measured is what the entry point did to the streams before it dispatched
    anything at all.
    """
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.setattr(sys, "stdout", a_windows_pipe())
    monkeypatch.setattr(sys, "stderr", a_windows_pipe())

    edullm_platform.cli.main([])

    assert sys.stdout.encoding == "utf-8"
    assert sys.stderr.encoding == "utf-8"
    print(BEYOND_THE_CODE_PAGE, file=sys.stdout)
    print(BEYOND_THE_CODE_PAGE, file=sys.stderr)


def test_the_streams_are_reconfigured_and_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: wrap ``sys.stdout.buffer`` in a second ``TextIOWrapper`` instead.

    It reads as the tidier fix and it puts two buffers on one descriptor. Argparse writes
    ``--help`` and every usage error straight to ``sys.stdout`` and ``sys.stderr``, so the
    wrapper would not catch them and what a researcher saw would be ordered by whichever
    buffer flushed first. Asserting identity is what stops that arriving as a tidy-up.
    """
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    stdout, stderr = a_windows_pipe(), a_windows_pipe()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    edullm_platform.cli.main([])

    assert sys.stdout is stdout
    assert sys.stderr is stderr
    # A lone surrogate is the one string UTF-8 will not encode under strict, and os.fsdecode
    # puts one in a path when Windows hands back UTF-16 that is not valid. This function exists
    # because printing raised, so printing must not raise.
    print("a path with \udcff in it", file=sys.stdout)


def test_an_encoding_the_researcher_asked_for_is_not_overruled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: reconfigure unconditionally.

    ``PYTHONIOENCODING`` is the documented way to say which codec you want, and the bug being
    fixed is the codec nobody chose -- the one the interpreter read off the locale. Overruling
    a stated intent is a different bug wearing the same fix, and it would make this binary the
    one program on the machine that ignores the variable.
    """
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setattr(sys, "stdout", a_windows_pipe())
    monkeypatch.setattr(sys, "stderr", a_windows_pipe())

    edullm_platform.cli.main([])

    assert sys.stdout.encoding == "cp1252"


def test_a_machine_readable_document_is_ascii_whatever_is_in_it() -> None:
    """Mutation: ``ensure_ascii=False`` in ``emit``, which reads better and is smaller.

    This is the half that matters more, and it is the half that was never broken -- ``--json``
    came out ASCII because of a default nobody had written down, so the fix here was to write
    it down. A crash on prose costs a person a retry; a truncated document costs a script its
    answer, and what the script reports is whatever it makes of half a file.

    Held over the bytes rather than over the call, because unlike the two defaults at the top
    of this file this one can be run: the escaping happens the same way on every platform, so
    a document that is ASCII here is ASCII on Windows.
    """
    document = {
        "run": "run_019fcf3c-9878",
        "detail": BEYOND_THE_CODE_PAGE,
        "checkpoint": "step 400 \u2192 s3://bucket/ren\u00e9e",
    }
    out = io.StringIO()

    emit(document, out=out)

    assert out.getvalue().isascii(), (
        "a --json document carries characters outside ASCII, so redirecting it on Windows "
        "truncates it at the first one and the caller parses half a document"
    )
    out.getvalue().encode("cp1252")
    assert json.loads(out.getvalue()) == document, "the escaping has to be lossless"


def test_the_document_survives_the_stream_that_kills_the_prose() -> None:
    """The two halves side by side, which is the only way the difference is visible.

    Mutation: none -- this is the comparison the case above is asserting, written out. The
    same characters, the same cp1252 stream, and a crash on one side and not on the other.
    """
    document = {"detail": BEYOND_THE_CODE_PAGE}

    with pytest.raises(UnicodeEncodeError):
        print(BEYOND_THE_CODE_PAGE, file=a_windows_pipe())

    emit(document, out=a_windows_pipe())
