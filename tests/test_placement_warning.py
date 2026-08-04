"""A shape that may not place is said so at submission time, and is still submittable.

``config/capacity.yaml`` recorded four shapes as ``places: unreliably`` and a substitution
for two of them, and nothing read the file. All four stayed in the form's dropdown, so the
only way to learn that one of them does not place was to submit and wait: a job that cannot
be placed sits in ``RUNNABLE`` with no error written anywhere, which is indistinguishable
from a job that is merely queued.

**The two failures worth testing here pull in opposite directions, and both have happened
to warnings in this repository before.** One is the warning not arriving -- a shape that
does not place going out with nothing said, which is the state before this module. The
other is the warning becoming a gate: a file whose own header says it records the account's
experience rather than a measurement is not something to refuse a submission on, and a
refusal here would make a shape that has quietly become available unaskable-for. So every
case below checks both halves: what is said, and that the submission still compiles.

The shipped file is read rather than a fixture copy of it, for the reason
``tests/test_compile_submission_cli.py`` gives about the rest of ``config/``: the values
that decide a submission's fate are in those files, and a fixture copy would be a second
answer to every question they settle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.placement import (
    CAPACITY_FILENAME,
    PLACES_RELIABLY,
    PLACES_UNRELIABLY,
    PlacementRecord,
    UnreadableCapacityError,
    placement_warning,
    read_capacity,
)
from tools.compile_submission import EXIT_OK, EXIT_UNUSABLE, main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
CAPACITY_PATH = CONFIG_DIR / CAPACITY_FILENAME

SUBMITTER = "caiiris"
REPOSITORY_URL = "https://github.com/edu-llm/OLMo-core"
RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
COMMIT_SHA = "8076c077533eb79742f4ed22aade439df123a593"
PUBLISHED_DIGEST = "sha256:" + "1a" * 32
FIRST_PUSH = "2026-07-26T09:02:00.000000Z"
SCANNED_AT = "2026-07-26T22:07:12.000000Z"

#: The two shapes the file records a substitute for, and the substitute it records.
SUBSTITUTIONS = {"gpu-4xa10g": "gpu-4xl4", "gpu-8xa100": "gpu-8xl40s"}

#: The two where the absence of a substitute is the recorded answer. Nothing else in the
#: catalog holds 80 GB on one device or 640 GB on one node.
NO_SUBSTITUTE = ("gpu-1xh100", "gpu-8xh100")


@pytest.fixture(scope="module")
def shipped() -> tuple[PlacementRecord, ...]:
    return read_capacity(CAPACITY_PATH)


def one_gpu_command() -> list[str]:
    return ["python", ".edullm/train_on_corpus.py", "$EDULLM_RUN_ID"]


def ranked_command(devices: int) -> list[str]:
    return [
        "python",
        "-m",
        "torch.distributed.run",
        f"--nproc-per-node={devices}",
        "--standalone",
        ".edullm/train_on_corpus.py",
        "$EDULLM_RUN_ID",
    ]


def form(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repository": "OLMo-core",
        "commit_sha": COMMIT_SHA,
        "workload_profile": "olmo-core-check",
        "dataset_release": "dolma-2026-07",
        "team": "data-prep",
        "wandb_project": "olmo-core-tokenize",
        "experiment": "placement-check",
        "command": ["python", "-m", "olmo_core.data.tokenize"],
        "compute_profile": "cpu-32vcpu",
    }
    payload.update(overrides)
    return payload


def resolved() -> dict[str, Any]:
    return {
        "published": [{"image_digest": PUBLISHED_DIGEST, "pushed_at": FIRST_PUSH}],
        "image_scan": {"schema_version": 1, "status": "COMPLETE", "scanned_at": SCANNED_AT},
    }


def compile_form(
    tmp_path: Path,
    *,
    payload: dict[str, object],
    config_dir: Path = CONFIG_DIR,
) -> tuple[int, str, dict[str, Any]]:
    """Run the real compile step and hand back its exit code, summary and output."""
    inputs = tmp_path / "submission-form.json"
    inputs.write_text(json.dumps(payload), encoding="utf-8")
    published = tmp_path / "published-image.json"
    published.write_text(json.dumps(resolved()), encoding="utf-8")
    output = tmp_path / "compiled-submission.json"
    summary = tmp_path / "approver-context.md"
    exit_code = main(
        [
            "--inputs",
            str(inputs),
            "--config-dir",
            str(config_dir),
            "--published-images",
            str(published),
            "--submitter",
            SUBMITTER,
            "--repository-url",
            REPOSITORY_URL,
            "--output",
            str(output),
            "--summary",
            str(summary),
            "--run-id",
            RUN_ID,
        ]
    )
    rendered = summary.read_text(encoding="utf-8") if summary.exists() else ""
    compiled = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    assert isinstance(compiled, dict)
    return exit_code, rendered, compiled


# ---------------------------------------------------------------------------------------
# What the shipped file says, read the way the submission path now reads it
# ---------------------------------------------------------------------------------------


def test_every_priced_shape_this_reads_carries_one_of_the_two_answers(
    shipped: tuple[PlacementRecord, ...],
) -> None:
    assert shipped
    assert {record.places for record in shipped} <= {PLACES_RELIABLY, PLACES_UNRELIABLY}
    assert len({record.profile for record in shipped}) == len(shipped)


def test_the_four_shapes_that_do_not_place_are_the_four_that_warn(
    shipped: tuple[PlacementRecord, ...],
) -> None:
    """Mutation: warn on every shape, or on none.

    Both are ways for this to look wired up and say nothing useful. A warning on every
    submission is one submitters learn to skip; a warning on none is the state before this
    existed, and neither fails anything else.
    """
    warned = {
        record.profile
        for record in shipped
        if placement_warning(record.profile, capacity=shipped) is not None
    }

    assert warned == {*SUBSTITUTIONS, *NO_SUBSTITUTE}


@pytest.mark.parametrize(("shape", "substitute"), sorted(SUBSTITUTIONS.items()))
def test_a_recorded_substitute_is_named_in_the_message(
    shape: str, substitute: str, shipped: tuple[PlacementRecord, ...]
) -> None:
    """Mutation: say only that the shape may not place.

    The substitution is the whole reason the file records more than a boolean, and a
    warning that withheld it would leave the submitter with the same problem and no next
    step -- which is what sends somebody back to the shape they already picked.
    """
    warning = placement_warning(shape, capacity=shipped)

    assert warning is not None
    assert f"`{substitute}`" in warning
    assert f"`{shape}`" in warning


@pytest.mark.parametrize("shape", NO_SUBSTITUTE)
def test_no_recorded_substitute_is_stated_as_an_answer_rather_than_left_blank(
    shape: str, shipped: tuple[PlacementRecord, ...]
) -> None:
    """Mutation: name the nearest smaller card anyway.

    ``config/workload-catalog.yaml`` records what happened the last time somebody did:
    two pieces of work asked for an H100, were offered the L40S as "the closest thing
    available", and it was not. A message that stayed silent about the absence would read
    as a lookup that broke rather than as an answer.
    """
    warning = placement_warning(shape, capacity=shipped)

    assert warning is not None
    assert "No substitute is recorded" in warning
    assert "absence is an answer" in warning
    assert not any(other in warning for other in SUBSTITUTIONS.values())


def test_the_message_claims_no_more_than_the_file_does(
    shipped: tuple[PlacementRecord, ...],
) -> None:
    """Mutation: say the shape will not place, or drop the sentence that says why not.

    ``config/capacity.yaml``'s header is explicit that its answers are the account's own
    experience rather than a probe, because "the measurement is 'did a job start' and the
    instrument costs an instance". A warning that read as a measurement would be a claim
    this repository cannot support, and the person acting on it would over-trust it.
    """
    warning = placement_warning("gpu-8xh100", capacity=shipped)

    assert warning is not None
    assert "may not place" in warning
    assert "will not place" not in warning
    assert "warning and not a refusal" in warning
    assert f"config/{CAPACITY_FILENAME}" in warning
    # The failure mode itself, because it is the thing a submitter has to recognise hours
    # later: there is no error anywhere to go looking for.
    assert "RUNNABLE" in warning


def test_a_shape_that_places_is_told_nothing(shipped: tuple[PlacementRecord, ...]) -> None:
    assert placement_warning("cpu-32vcpu", capacity=shipped) is None
    assert placement_warning("gpu-1xa10g", capacity=shipped) is None


def test_a_shape_the_file_does_not_record_is_unknown_rather_than_fine() -> None:
    """Mutation: return ``None`` for a profile with no entry.

    That is the denylist failure ``config/capacity.yaml``'s header names: a file listing
    only the scarce shapes assumes the next promotion places, and nobody finds out
    otherwise until somebody waits four hours. Every priced shape is meant to be in the
    file, so this should be unreachable against the shipped configuration -- and it is the
    reachable-by-accident case that matters.
    """
    warning = placement_warning("gpu-16xz9000", capacity=())

    assert warning is not None
    assert "No placement answer is recorded" in warning
    assert "RUNNABLE" in warning


# ---------------------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("profiles: []\n", "no entry"),
        ("[]\n", "not a top-level mapping"),
        ("schema_version: 1\n", "lists no profiles"),
        ("profiles:\n  - profile: a\n    places: sometimes\n", "does not name a profile"),
        ("profiles:\n  - profile: a\n    places: reliably\n    offer_instead: 7\n", "not a"),
    ],
    ids=["empty", "sequence", "no profiles key", "invented answer", "substitute is a number"],
)
def test_a_document_that_is_not_a_placement_answer_is_refused(
    tmp_path: Path, document: str, expected: str
) -> None:
    """Mutation: fall back to an empty list of records.

    That default reads as "every shape places", so a file that stopped parsing would take
    the only warning about a four-hour wait with it and leave every submission looking
    exactly as it did before.
    """
    path = tmp_path / CAPACITY_FILENAME
    path.write_text(document, encoding="utf-8")

    if expected == "no entry":
        assert read_capacity(path) == ()
        return
    with pytest.raises(UnreadableCapacityError):
        read_capacity(path)


def test_two_answers_for_one_shape_are_refused_rather_than_resolved(tmp_path: Path) -> None:
    """The reviewed-config loader's duplicate-key rule, applied to this file too.

    ``yaml.safe_load`` takes the second of two identical keys, so a shape recorded twice
    would resolve to whichever entry was written later -- which is not a thing a reviewer
    reading the diff would see.
    """
    path = tmp_path / CAPACITY_FILENAME
    path.write_text(
        "profiles:\n"
        "  - profile: gpu-4xa10g\n"
        "    places: unreliably\n"
        "    places: reliably\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="duplicate mapping key"):
        read_capacity(path)


# ---------------------------------------------------------------------------------------
# Through the submission path a submitter actually meets
# ---------------------------------------------------------------------------------------


def test_an_unreliable_shape_still_compiles_and_carries_the_warning(tmp_path: Path) -> None:
    """The whole point, in one case: told at submission time, and not stopped.

    Mutation: raise ``SubmissionRefusedError`` instead. The suite would still be green
    everywhere else and the platform would have turned a file of remembered experience
    into a gate.
    """
    exit_code, summary, compiled = compile_form(
        tmp_path,
        payload=form(compute_profile="gpu-4xa10g", command=ranked_command(4)),
    )

    assert exit_code == EXIT_OK
    assert compiled["manifest"]["compute_profile"] == "gpu-4xa10g"
    assert "`gpu-4xa10g` may not place" in summary
    assert "`gpu-4xl4`" in summary


def test_a_shape_with_no_substitute_compiles_and_says_there_is_none(tmp_path: Path) -> None:
    exit_code, summary, compiled = compile_form(
        tmp_path,
        payload=form(compute_profile="gpu-1xh100", command=one_gpu_command()),
    )

    assert exit_code == EXIT_OK
    assert compiled["manifest"]["compute_profile"] == "gpu-1xh100"
    assert "`gpu-1xh100` may not place" in summary
    assert "No substitute is recorded" in summary


def test_a_shape_that_places_puts_nothing_in_front_of_the_approver(tmp_path: Path) -> None:
    """Mutation: render the heading unconditionally, empty.

    A section that appears on every submission and says nothing on almost all of them is
    the version of this warning nobody reads by the fifth run.
    """
    exit_code, summary, _ = compile_form(tmp_path, payload=form())

    assert exit_code == EXIT_OK
    assert "may not place" not in summary
    assert "placement" not in summary.lower()


def test_the_warning_is_written_where_the_submitter_is_already_looking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The step summary is the record; the job log is what is open while it happens.

    Mutation: write it only into the summary. GitHub renders a step summary on the run
    page and the compile job's own log is what a submitter watches, so a warning in one
    and not the other reaches whichever of the two people happens to look there.
    """
    compile_form(
        tmp_path,
        payload=form(compute_profile="gpu-8xa100", command=ranked_command(8)),
    )

    assert "`gpu-8xa100` may not place" in capsys.readouterr().err


def test_a_capacity_file_that_cannot_be_read_is_an_unusable_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: catch it and carry on with no warning.

    A refusal would be wrong -- the shape may be fine -- but so is compiling a submission
    while the one file that would have warned about it is unreadable. The compile step
    already treats every other piece of unreadable reviewed configuration this way.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for path in CONFIG_DIR.glob("*.yaml"):
        (config_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    # The copy is otherwise the reviewed configuration, so an unusable verdict here can
    # only be about this file rather than about something the copy dropped.
    assert compile_form(tmp_path, payload=form(), config_dir=config_dir)[0] == EXIT_OK
    (config_dir / CAPACITY_FILENAME).write_text("profiles: not-a-list\n", encoding="utf-8")

    exit_code, _, _ = compile_form(tmp_path, payload=form(), config_dir=config_dir)

    assert exit_code == EXIT_UNUSABLE
    assert CAPACITY_FILENAME in capsys.readouterr().err
