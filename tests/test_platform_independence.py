"""Two defaults that mean different things on different machines, and are named here instead.

**BOTH OF THESE ARE CORRECT BY ACCIDENT ON THE MACHINE EVERY CONTRIBUTOR USES.** A test that
ran the code and compared the answer would pass on macOS and on the Ubuntu runner whichever way
the code went, which is why neither is asserted by running it. What is asserted is that the
argument deciding the behaviour is written down, because writing it down is the whole fix.

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
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Final

import pytest

from edullm_platform.cli.configuration import load_reviewed_configuration
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
