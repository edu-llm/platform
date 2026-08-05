"""The document a machine reads, and the two things it must never be.

WHY THIS EXISTS AT ALL. Every skill under .cursor/skills/ matches on a refusal code, and
before --json the only way to get one was to match the word after "refused" in a wrapped
paragraph. docs-frank/reference/designing-the-cli.md settles the shape: one document on
stdout whatever the outcome, the key names tools/compile_submission.py already writes, a
format_version, and a flag rather than a terminal check.

THE TWO THINGS IT MUST NEVER BE ARE BOTH ASSERTED RATHER THAN INTENDED. It must never carry
the placeholder image digest, because a caller reading image_digest out of a check would be
reading sha256 followed by sixty-four zeroes and could compare it to a real one. And it must
never make check reach a network, because that is the property the whole verb is built on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.cli.machine import FORMAT_VERSION
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.cli.preflight import UNRESOLVED_IMAGE_DIGEST
from tests.cli_support import FakeRunner, git_answers, invoke, write_spec


def checkout(tmp_path: Path, **spec: object) -> tuple[Path, FakeRunner]:
    write_spec(tmp_path, **spec)  # type: ignore[arg-type]
    return tmp_path, FakeRunner(git_answers(tmp_path))


def only_document(out: str) -> dict[str, Any]:
    """The whole of stdout as one JSON document, which is the contract being asserted."""
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    return parsed


def test_check_json_is_one_document_on_stdout_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep printing the human blocks alongside the document.

    A caller pipes stdout straight into a parser. One stray line above the brace and every
    skill in this repository stops working, and it stops working with a JSONDecodeError
    rather than with anything a reader could act on.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert document["format_version"] == FORMAT_VERSION
    assert document["verb"] == "check"
    assert document["refused"] is False
    assert document["refusals"] == []


def test_check_json_reaches_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: fill submitter in from `gh api user` when the config file has nobody.

    check answers in a fraction of a second and asks nothing, which is what makes it the
    verb somebody runs half a dozen times while editing a spec and the verb that works on a
    login node with no egress. A serializer is not a reason to give that up.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("gh") == []


def test_a_refused_check_still_emits_one_document_and_still_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: emit the document only on the clear path, or exit 0 because it printed.

    Refused is the common case and the interesting one. The exit code is the published
    interface and the document is the detail, and a caller needs both to agree: exit 1 with
    a document naming which codes fired.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        [
            "check",
            "--json",
            "--dataset",
            "a-corpus-nothing-registers",
            "--experiment",
            "an-experiment",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    document = only_document(out)
    assert document["refused"] is True
    assert "unregistered_dataset" in [refusal["code"] for refusal in document["refusals"]]
    assert all({"code", "detail"} == set(refusal) for refusal in document["refusals"])


def test_the_placeholder_digest_is_nowhere_in_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: dump the manifest as pydantic serializes it and stop there.

    cli/preflight.py builds a real RunManifest with UNRESOLVED_IMAGE_DIGEST standing in for
    the one field a laptop cannot fill, and its docstring promises the value is never
    printed. A human renderer kept that promise by printing "resolved at submit, from the
    commit above". A serializer that dumped the model would break it silently, and what it
    would publish is a well-formed digest naming nothing, which a caller could compare
    against a real one and get a false answer from.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert UNRESOLVED_IMAGE_DIGEST not in out
    document = only_document(out)
    assert document["manifest"] is not None
    assert document["manifest"]["image_digest"] is None
    assert document["manifest_sha256"] is None


def test_the_document_carries_the_two_checks_a_laptop_could_not_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave `deferred` out, so a clean check reads as a clean bill of health.

    docs-frank/working/adarsh-rajesh-first-run.md is a transcript of what it costs when a
    submitter believes a clean preflight means a submission will go through. An agent
    believes it harder and faster than a person does.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert [entry["code"] for entry in document["deferred"]] == [
        "no_published_image",
        "image_scan_findings_unreviewed",
    ]


def test_money_is_a_string_and_not_a_float(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: `float(cost.maximum_compute_cost_usd)`.

    presentation.py's own header records the defect this repeats: a CLI that rounded
    differently from the approver page would have a submitter and a lead reading two prices
    for one run. Binary floating point is a second arithmetic, and the whole path carries
    money as base-ten text for exactly that reason. Asserted as a type rather than as a
    value, because the value is the catalog's and this test is not about the catalog.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    cost = only_document(out)["cost"]
    assert isinstance(cost["maximum_compute_cost_usd"], str)
    assert isinstance(cost["hourly_rate_usd"], str)
    assert isinstance(cost["nodes"], int)


def test_the_document_is_byte_identical_piped_or_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: sort keys only sometimes, or indent by terminal width.

    Nothing in this binary calls isatty and nothing here starts. Two runs of one command
    against one tree produce one string, which is what makes a pasted transcript the
    transcript somebody else sees and what makes a golden test possible at all.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")
    argv = ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"]

    first = invoke(argv, runner=runner, cwd=root, monkeypatch=monkeypatch)
    second = invoke(argv, runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert first[1] == second[1]
    assert "\x1b" not in first[1]


def test_check_without_the_flag_still_prints_the_paragraphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: make JSON the primary form, or switch on whether stdout is a terminal.

    designing-the-cli.md rules both out in the same paragraph, and the reason is that
    `edullm check > note.txt` and `edullm check` would then disagree about what was checked.
    This is the assertion that keeps the flag a flag.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert out.startswith("checked against ")
    assert "no refusals. edullm submit will dispatch this." in out
