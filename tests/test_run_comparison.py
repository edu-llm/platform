"""What two runs of one submission may differ about, and what they may not."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.run_comparison import (
    IDENTICAL_FIELDS,
    REQUIRED_FIELD_FAMILIES,
    REQUIRED_FIELDS,
    RecordedRun,
    RecordField,
    cause_for,
    compare_runs,
    flatten,
    read_run,
    required_field_coverage,
    unexplained,
)

LEFT = "run_019fcdf1-e3d7-7033-a2de-9320a987d72c"
RIGHT = "run_019fce18-8684-70e9-86ab-b809f3cdfa4c"

#: The two runs happen at two times. Written as an explicit mapping rather than derived from
#: the ids, because both of these real run ids end in the same character and a fixture that
#: derived a timestamp from one would silently give the two runs the same clock reading --
#: which makes the comparison stop testing the one cause it most needs to explain.
MINUTE = {LEFT: "00", RIGHT: "41"}


def records(run_id: str, *, wandb_project: str = "edullm-platform-smoke") -> dict[str, object]:
    """One run's three records, as the lineage bucket holds them."""
    minute = MINUTE[run_id]
    return {
        "intent": {
            "schema_version": 1,
            "run_id": run_id,
            "submitter": "philote",
            "manifest_sha256": "sha256:" + "a" * 64,
            "approving_environment": "run-approval-automatic",
            "recorded_at": f"2026-08-05T10:{minute}:00Z",
            "manifest": {
                "schema_version": 1,
                "repository": "OLMo-core",
                "commit_sha": "b" * 40,
                "image_digest": "sha256:" + "c" * 64,
                "workload_profile": "olmo-core-check",
                "compute_profile": "gpu-1xt4",
                "dataset_release": "none",
                "team": "platform",
                "wandb_project": wandb_project,
                "command": ["python", "-c", "print(1)"],
                "maximum_runtime_hours": "0.5",
                "maximum_attempts": 1,
                "checkpoint": None,
                "fanout": None,
            },
            "workflow_run": {
                "run_repository": "edu-llm/platform",
                "workflow_repository": "edu-llm/platform",
                "workflow_path": ".github/workflows/submit-run.yml",
                "workflow_ref": "refs/heads/main",
                # Ten digits, where a real dispatch id is eleven. An eleven-digit integer
                # literal in a tracked .py file is refused by the account-id scan in
                # tests/test_evidence.py, because zero-padded to twelve it has the shape of
                # an account id. What this fixture needs of the number is only that the two
                # runs carry different ones.
                "run_id": 3093673935 if run_id == LEFT else 3093680000,
                "run_attempt": 1,
            },
        },
        "decision": {
            "schema_version": 1,
            "run_id": run_id,
            "manifest_sha256": "sha256:" + "a" * 64,
            "policy_version": "v3",
            "approval_class": "automatic",
            "approving_environment": "run-approval-automatic",
            "accepted": True,
            "reason": "accepted",
            "detail": "Admitted as automatic under policy v3.",
            "recorded_at": f"2026-08-05T10:{minute}:00Z",
            "authorization": {"granted": True, "reason": "automatic_release"},
            "cost": {"cells": 1, "nodes": 1, "maximum_attempts": 1},
        },
        "result": {
            "schema_version": 1,
            "run_id": run_id,
            "attempt_id": f"att_{run_id[4:]}",
            "outcome": "succeeded",
            "exit_code": 0,
            "retention_class": "standard",
            "completed_at": f"2026-08-05T11:{minute}:00Z",
            "output_prefixes": [
                f"s3://sbsandbox-intern-edullm-outputs/teams/platform/runs/{run_id}/"
            ],
            "checkpoints": [],
            "checkpoint_survey": {
                "schema_version": 1,
                "outcome": "listed",
                "objects_seen": 2,
                "bytes_seen": 1_073_741_824,
                "unparsed_directories": [],
            },
            "wandb_run": {
                "entity": "eduLLM",
                "project": wandb_project,
                "run_id": run_id,
            },
        },
    }


def written(root: Path, run_id: str, **kwargs: object) -> None:
    bundle = records(run_id, **kwargs)  # type: ignore[arg-type]
    for prefix in ("intent", "decision", "result"):
        target = root / prefix / f"{run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(bundle[prefix]), encoding="utf-8")
    attempt = root / "attempt" / run_id / f"att_{run_id[4:]}.json"
    attempt.parent.mkdir(parents=True, exist_ok=True)
    attempt.write_text("{}", encoding="utf-8")


def test_two_runs_of_one_submission_differ_only_in_named_causes(tmp_path: Path) -> None:
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)

    differences = compare_runs(read_run(tmp_path, LEFT), read_run(tmp_path, RIGHT))

    assert differences, "two runs carry different ids and times, so something must differ"
    assert unexplained(differences) == ()


def test_a_difference_no_cause_explains_is_reported_as_unexplained(tmp_path: Path) -> None:
    """Mutation: treat every difference as expected.

    The whole value of this comparison is that it can say no. A run whose W&B project
    changed between the two submissions is not the same submission run twice, and a
    comparison that shrugged at it would certify a done-condition nobody met.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT, wandb_project="somewhere-else")

    differences = compare_runs(read_run(tmp_path, LEFT), read_run(tmp_path, RIGHT))

    assert "intent.manifest.wandb_project" in unexplained(differences)
    assert "result.wandb_run.project" in unexplained(differences)


def without(run: RecordedRun, *paths: str) -> RecordedRun:
    """One run's records with some leaves gone, the way a schema change leaves them."""
    return run.model_copy(
        update={"fields": tuple(field for field in run.fields if field.path not in paths)}
    )


def test_the_manifest_digest_is_required_to_be_present_and_equal(tmp_path: Path) -> None:
    """Mutation: check only that nothing differs.

    An empty document differs from an empty document in nothing at all. IDENTICAL_FIELDS is
    checked positively for that reason: a record missing the field is a record that stopped
    being compared, and the absence of a difference is not evidence of agreement.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    left = read_run(tmp_path, LEFT)
    right = read_run(tmp_path, RIGHT)

    stripped = required_field_coverage(without(left, "intent.manifest_sha256"), right)
    assert stripped.missing == ("intent.manifest_sha256",)
    assert stripped.unverified == ()

    whole = required_field_coverage(left, right)
    assert whole.missing == ()
    assert whole.unverified == ()


def test_a_required_field_neither_run_carries_is_unverified_and_not_agreement(
    tmp_path: Path,
) -> None:
    """THE ONE THAT MATTERS. Mutation: gather the required paths from the records.

    That is what this did, and it is a check that cannot fail in the case it was written
    for. Requiring only the paths at least one record carries means a field dropped from
    BOTH sides is never in the required set, is never looked at, and produces no difference
    -- so the report reads exactly like agreement. Measured against the lineage store on
    2026-08-04, a July pair passed with five required fields unexamined, ``result.exit_code``
    among them.

    Absent from both is its own answer and not either of the other two. It is not a
    difference: nothing was compared, so nothing can be said to differ. It is not agreement
    for the same reason.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    gone = ("result.exit_code", "result.wandb_run.entity", "result.wandb_run.project")
    left = without(read_run(tmp_path, LEFT), *gone)
    right = without(read_run(tmp_path, RIGHT), *gone)

    coverage = required_field_coverage(left, right)

    assert coverage.unverified == tuple(sorted(gone))
    # And not folded into the other answer, which would report two runs as differing over a
    # field neither of them has -- wrong about the runs, and wrong on every historical pair.
    assert coverage.missing == ()
    assert compare_runs(left, right) == compare_runs(
        read_run(tmp_path, LEFT), read_run(tmp_path, RIGHT)
    )


def test_a_family_with_no_members_is_an_empty_collection_and_not_a_missing_field(
    tmp_path: Path,
) -> None:
    """Mutation: report a required family that matched nothing as unverified.

    ``result.checkpoints[N].size_bytes`` is required of every checkpoint a run wrote, and
    the fixture runs wrote none. There is no honest count of what is absent there: a run
    with no checkpoints is not a run whose checkpoint fields went missing. Reporting one
    would put a permanent unverified line on every check-shaped run in the store, which is
    the crying wolf that makes the section above worth reading.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)

    coverage = required_field_coverage(read_run(tmp_path, LEFT), read_run(tmp_path, RIGHT))

    assert any(one.pattern.startswith("^result\\.checkpoints") for one in REQUIRED_FIELD_FAMILIES)
    assert not any(path.startswith("result.checkpoints") for path in coverage.unverified)


def test_every_required_field_is_named_or_belongs_to_a_family_and_never_both() -> None:
    """The two halves of the required set are one set, and the derived patterns prove it.

    IDENTICAL_FIELDS is what asks a path whether it is required, and REQUIRED_FIELDS is what
    the coverage check looks for by name. A named path a family also matches would be
    reported unverified while its family said the count was the data's business, which is
    two answers to one question.
    """
    for path in REQUIRED_FIELDS:
        assert any(one.fullmatch(path) for one in IDENTICAL_FIELDS), path
        assert not any(one.fullmatch(path) for one in REQUIRED_FIELD_FAMILIES), path
    assert len(set(REQUIRED_FIELDS)) == len(REQUIRED_FIELDS)


def test_a_second_attempt_is_a_difference_even_though_attempt_records_are_not_walked(
    tmp_path: Path,
) -> None:
    """Mutation: ignore attempt/ entirely.

    An attempt record's every leaf is an id or a time, so walking it would add noise. How
    many of them there are is not noise: a run that needed two attempts and a run that
    needed one are not the same run twice, and result.attempt_id -- the only other place it
    shows -- is in the varying set by construction.
    """
    written(tmp_path, LEFT)
    written(tmp_path, RIGHT)
    second = tmp_path / "attempt" / RIGHT / "att_second.json"
    second.write_text("{}", encoding="utf-8")

    differences = compare_runs(read_run(tmp_path, LEFT), read_run(tmp_path, RIGHT))

    assert "attempt_count" in unexplained(differences)


def test_a_checkpoint_digest_may_differ_and_its_size_may_not() -> None:
    """Mutation: excuse every checkpoint field, or excuse none of them.

    torch.save writes a byte length fixed by shapes and dtypes whatever the values are, so a
    size that moved is a checkpoint that changed shape or was truncated. The digest over
    those bytes is not fixed: floating-point reduction order on a GPU is not pinned by a
    seed, and this platform has never claimed a workload is bit-reproducible. Excusing both
    would let a truncated checkpoint through; excusing neither would rest the spine's
    done-condition on a claim nobody made.
    """
    assert cause_for("result.checkpoints[0].checksum") is not None
    assert cause_for("result.checkpoints[0].size_bytes") is None
    assert any(
        pattern.fullmatch("result.checkpoints[0].size_bytes") for pattern in IDENTICAL_FIELDS
    )


def test_flattening_gives_one_leaf_per_line_with_json_encoded_values() -> None:
    """A string and the number that prints the same way must not compare equal."""
    fields = flatten({"a": 1, "b": {"c": ["x", "y"]}}, prefix="r")

    assert fields == (
        RecordField(path="r.a", value="1"),
        RecordField(path="r.b.c[0]", value='"x"'),
        RecordField(path="r.b.c[1]", value='"y"'),
    )


def test_a_record_stored_as_a_json_string_holding_json_is_read(tmp_path: Path) -> None:
    """The state machine writes the handler's canonical bytes rather than re-encoding them,
    so some records in the bucket are a JSON string whose content is JSON. A reader that saw
    those as unparseable would silently compare two runs on the records it happened to
    understand."""
    written(tmp_path, LEFT)
    intent = tmp_path / "intent" / f"{LEFT}.json"
    intent.write_text(json.dumps(intent.read_text(encoding="utf-8")), encoding="utf-8")

    assert read_run(tmp_path, LEFT).run_id == LEFT


def test_a_run_whose_records_are_not_all_there_is_refused(tmp_path: Path) -> None:
    written(tmp_path, LEFT)
    (tmp_path / "result" / f"{LEFT}.json").unlink()

    with pytest.raises(FileNotFoundError) as missing:
        read_run(tmp_path, LEFT)

    assert "result" in str(missing.value)


def test_the_recorded_run_model_refuses_a_repeated_path() -> None:
    """Mutation: build the field map from a list that may carry one path twice.

    field_map() is a dict comprehension, so a duplicated path silently keeps the last
    value and drops the first. Two runs compared through that would agree about a field
    neither of them actually agreed about.
    """
    with pytest.raises(ValueError, match="once"):
        RecordedRun(
            run_id=LEFT,
            attempt_count=1,
            fields=(
                RecordField(path="intent.run_id", value='"a"'),
                RecordField(path="intent.run_id", value='"b"'),
            ),
        )
