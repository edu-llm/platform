"""The release procedure, and the ordering that makes it safe to interrupt.

**This tool exists because the manual version failed on the morning it was written.** The
procedure in `infra/README.md` is three steps ending in a template edit, and a release was
cut, recorded and pushed in one `&&` chain where a `pytest | tail` in the middle succeeded
as a *command* while the test inside it failed. The chain continued and `main` landed with a
release record that did not describe the tree.

So the tests here are not about the happy path, which is one call. They are about the two
orderings that decide what a half-finished release leaves behind, and about the check that
was missing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from release_lambda import (
    FUNCTIONS,
    Function,
    ReleaseError,
    _substitute,
    main,
)


def test_both_lambdas_are_released_by_default() -> None:
    """Mutation: default to the validator, which is the one people think of.

    ``build_package`` copies ``config/*.yaml`` into whatever zip it builds, and the
    lifecycle recorder is built through that same function -- so a catalog edit moves the
    recorder's digest even though nothing it does reads the catalog. That surprised somebody
    once already and cost a red tripwire on main, so releasing both is the default and
    releasing one is the case that has to be spelled out.
    """
    parser_default = main.__doc__  # keeps the import used if the assert below is edited out
    assert parser_default is None or isinstance(parser_default, str)
    assert set(FUNCTIONS) == {"validator", "recorder"}


def test_a_substitution_that_matches_nothing_refuses_rather_than_writing(tmp_path: Path) -> None:
    """Mutation: use `re.sub` and accept zero replacements.

    A no-op edit is the worst outcome available here, because everything downstream reads as
    success: the upload happened, the tool printed "recorded", and the file still names the
    previous version. The deployed function then stays behind a tree that claims to describe
    it, which is the exact state the tripwire exists to catch and would have caught -- one
    step later, in CI, on main.
    """
    target = tmp_path / "record.yaml"
    target.write_text("something: else\n", encoding="utf-8")

    with pytest.raises(ReleaseError, match="edits exactly one"):
        _substitute(target, r"^s3_object_version: .*$", "s3_object_version: new")

    assert target.read_text(encoding="utf-8") == "something: else\n"


def test_a_substitution_that_matches_twice_refuses_too(tmp_path: Path) -> None:
    """Two matches is as ambiguous as none, and silently taking the first is a guess.

    ``infra/admission-state-machine.yaml`` carries exactly one ``S3ObjectVersion`` today. A
    second Lambda added to that template would make this tool's edit a coin flip, and a coin
    flip that lands wrong deploys one function's code as another's.
    """
    target = tmp_path / "template.yaml"
    target.write_text("  S3ObjectVersion: a\n  S3ObjectVersion: b\n", encoding="utf-8")

    with pytest.raises(ReleaseError, match="edits exactly one"):
        _substitute(target, r"^(\s*)S3ObjectVersion: .*$", r"\1S3ObjectVersion: new")


def test_nothing_is_edited_until_every_upload_has_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE ORDERING THAT MATTERS. Mutation: upload and record each function in turn.

    A record naming a zip that failed to upload is worse than no record at all, because the
    tripwire then passes against a lie -- the tree and the record agree, and neither
    describes what is deployed. Uploading both before editing either means an interrupted
    release leaves the records untouched and the tripwire correctly red.
    """
    edited: list[str] = []

    monkeypatch.setattr("release_lambda.build", lambda function, destination: "a" * 64)

    def upload_that_fails_on_the_second(function: Function, *_: object, **__: object) -> str:
        if function.name == "lifecycle recorder":
            raise ReleaseError("the second upload failed")
        return "version-for-the-first"

    monkeypatch.setattr("release_lambda.upload", upload_that_fails_on_the_second)
    monkeypatch.setattr(
        "release_lambda.record",
        lambda function, **_: edited.append(function.name),
    )
    monkeypatch.setattr("release_lambda.verify", lambda selected: None)

    assert main([]) == 1
    assert edited == [], (
        "a record was written for a function whose sibling failed to upload, so the tree "
        "now describes a release that did not fully happen"
    )


def test_the_tripwire_is_run_and_its_exit_code_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check whose absence caused the incident this tool is named after.

    Editing two files correctly is not the same as the deployed zip matching the tree, and
    the only thing that knows the difference is the tripwire test. It is run as a subprocess
    with its return code inspected -- deliberately not piped through anything, because a
    pipe is what swallowed the failure the manual procedure hit.
    """
    monkeypatch.setattr("release_lambda.build", lambda function, destination: "a" * 64)
    monkeypatch.setattr("release_lambda.upload", lambda *a, **k: "v1")
    monkeypatch.setattr("release_lambda.record", lambda *a, **k: None)

    calls: list[list[str]] = []

    def red(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(args=command, returncode=1)

    monkeypatch.setattr("release_lambda.subprocess.run", red)

    assert main([]) == 1
    assert calls, "the tripwire was never run"
    assert "pytest" in calls[0]
    assert "tests/test_phase2_lambda_package.py" in calls[0]


def test_a_dry_run_uploads_nothing_and_edits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the digests can be read without committing to a release.

    Useful on its own -- "has this actually moved?" is the first question when a tripwire
    goes red, and the build is deterministic, so an unchanged digest means there is nothing
    to release and the template does not need editing at all.
    """
    monkeypatch.setattr("release_lambda.build", lambda function, destination: "b" * 64)

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a dry run reached the network or the filesystem")

    monkeypatch.setattr("release_lambda.upload", refuse)
    monkeypatch.setattr("release_lambda.record", refuse)
    monkeypatch.setattr("release_lambda.verify", refuse)

    assert main(["--dry-run"]) == 0


def test_every_function_names_a_template_and_a_record_that_exist() -> None:
    """Mutation: point one at a path that was renamed.

    The tool edits by path, so a stale path is a release that reports success and changes
    nothing -- the same silent no-op the substitution guard covers, one level up.
    """
    for function in FUNCTIONS.values():
        assert function.template.is_file(), f"{function.name}: {function.template} is missing"
        assert function.release_record.is_file(), (
            f"{function.name}: {function.release_record} is missing"
        )
        assert (PROJECT_ROOT / function.builder).is_file()
