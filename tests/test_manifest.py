import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from edullm_platform.canonical import canonical_json_bytes, sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.manifest_helpers import (
    COMMIT_SHA_REGEX,
    IMAGE_DIGEST_REGEX,
    REPRESENTATIVE_MANIFEST_COSTS,
    compute_manifest_maximum_cost,
    is_compute_profile_registered,
    is_workload_profile_registered,
    load_manifest,
    manifest_has_immutable_image,
    manifest_has_immutable_revision,
)
from edullm_platform.operational_inventory import REQUIRED_REPRESENTATIVE_MANIFESTS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_FIXTURES_DIR = PROJECT_ROOT / "fixtures" / "manifests"

REPRESENTATIVE_MANIFEST_FILENAMES = tuple(
    sorted(path.name for path in MANIFEST_FIXTURES_DIR.glob("*.yaml"))
)

EC2_TRAINING_MANIFEST = "gpu-routine.yaml"
SAGEMAKER_TRAINING_MANIFEST = "sagemaker-routine.yaml"
BRANCH_EXPERIMENT_MANIFEST = "olmo-branch-routine.yaml"
MULTISEED_MANIFEST = "multiseed-routine.yaml"

BACKEND_TOKENS = ("sagemaker", "backend", "ec2", "batch", "cluster", "queue")

REQUIRED_MANIFEST_FIELDS = tuple(
    name for name, field in RunManifest.model_fields.items() if field.is_required()
)

INFRASTRUCTURE_ESCAPE_HATCHES: tuple[tuple[str, object], ...] = (
    ("iam_role", "arn:aws:iam::sandbox:role/edullm-runner"),
    ("job_queue", "edullm-gpu-priority"),
    ("subnet_ids", ["subnet-0a1b2c3d4e5f", "subnet-1a2b3c4d5e6f"]),
    ("security_group_ids", ["sg-0a1b2c3d4e5f"]),
    ("instance_type", "g5.48xlarge"),
    ("mounts", [{"source": "/mnt/scratch", "target": "/scratch"}]),
    ("volumes", [{"name": "scratch", "size_gb": 500}]),
)


def load_representative_manifest(filename: str) -> RunManifest:
    return load_manifest(MANIFEST_FIXTURES_DIR / filename)


def load_manifest_document(filename: str) -> dict[str, object]:
    document = yaml.safe_load((MANIFEST_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def reverse_mapping_order(document: dict[str, object]) -> dict[str, object]:
    return {
        key: reverse_mapping_order(value) if isinstance(value, dict) else value
        for key, value in reversed(list(document.items()))
    }


def load_workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def differing_fields(left: RunManifest, right: RunManifest) -> set[str]:
    return {
        field for field in RunManifest.model_fields if getattr(left, field) != getattr(right, field)
    }


__all__ = (
    "BRANCH_EXPERIMENT_MANIFEST",
    "COMMIT_SHA_REGEX",
    "EC2_TRAINING_MANIFEST",
    "IMAGE_DIGEST_REGEX",
    "MANIFEST_FIXTURES_DIR",
    "MULTISEED_MANIFEST",
    "PROJECT_ROOT",
    "REPRESENTATIVE_MANIFEST_COSTS",
    "REPRESENTATIVE_MANIFEST_FILENAMES",
    "SAGEMAKER_TRAINING_MANIFEST",
    "compute_manifest_maximum_cost",
    "is_compute_profile_registered",
    "is_workload_profile_registered",
    "load_representative_manifest",
    "load_workload_catalog",
    "manifest_has_immutable_image",
    "manifest_has_immutable_revision",
)


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "dataset_release": "dolma-2026-07",
        "command": ["python", "-m", "train"],
        "team": "modeling",
        "wandb_project": "olmo",
        "workload_profile": "gpu-training-smoke",
        "compute_profile": "gpu-single-node",
        "maximum_runtime_hours": "6",
        "maximum_attempts": 2,
        "checkpoint": {
            "interval_minutes": 30,
            "destination_prefix": "s3://sbsandbox-intern-edullm-checkpoints/runs/",
            "resume_required": True,
        },
    }


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    loc: tuple[str | int, ...],
    message_fragment: str | None = None,
) -> None:
    matching_errors = [
        item for item in error.errors() if item["type"] == error_type and item["loc"] == loc
    ]
    assert matching_errors, (
        f"expected error type {error_type!r} at loc {loc!r}, got {error.errors()}"
    )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages at {loc!r}, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def test_manifest_validates_complete_payload() -> None:
    manifest = RunManifest.model_validate(manifest_payload())
    assert manifest.repository == "OLMo-core"
    assert manifest.commit_sha == "a" * 40
    assert manifest.command == ("python", "-m", "train")
    assert manifest.maximum_runtime_hours == Decimal(6)
    assert manifest.maximum_attempts == 2
    assert manifest.checkpoint is not None
    assert manifest.checkpoint.resume_required is True


def test_manifest_rejects_mutable_commit_sha() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = "main"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_mutable_image_digest() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "latest"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_uppercase_commit_sha() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = "A" * 40
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_short_commit_sha() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = "a" * 7
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_commit_sha_with_trailing_suffix() -> None:
    payload = manifest_payload()
    payload["commit_sha"] = ("a" * 40) + "extra"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("commit_sha",),
    )


def test_manifest_rejects_image_digest_with_trailing_tag() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "sha256:" + ("b" * 64) + ":latest"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_non_sha256_image_digest() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "sha512:" + ("b" * 64)
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_bare_image_digest_without_algorithm_prefix() -> None:
    payload = manifest_payload()
    payload["image_digest"] = "b" * 64
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="string_pattern_mismatch",
        loc=("image_digest",),
    )


def test_manifest_rejects_unordered_command() -> None:
    payload = manifest_payload()
    payload["command"] = {"python", "-m", "train"}
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("command",),
        message_fragment="ordered sequences must be provided as a list or tuple",
    )


def test_manifest_rejects_retryable_without_checkpoint() -> None:
    payload = manifest_payload()
    payload["checkpoint"] = None
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=(),
        message_fragment="retryable workloads require a checkpoint contract",
    )


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("schema_version", 2, "literal_error"),
        ("repository", "", "string_too_short"),
        ("dataset_release", "", "string_too_short"),
        ("team", "", "string_too_short"),
        ("wandb_project", "", "string_too_short"),
        ("workload_profile", "", "string_too_short"),
        ("compute_profile", "", "string_too_short"),
        ("maximum_attempts", 0, "greater_than_equal"),
    ],
)
def test_manifest_rejects_invalid_field_values(
    field: str,
    value: object,
    error_type: str,
) -> None:
    payload = manifest_payload()
    payload[field] = value
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type=error_type,
        loc=(field,),
    )


def test_the_manifest_payload_this_module_uses_supplies_every_required_field() -> None:
    assert set(REQUIRED_MANIFEST_FIELDS) <= set(manifest_payload())


@pytest.mark.parametrize("field", REQUIRED_MANIFEST_FIELDS)
def test_manifest_rejects_a_payload_that_omits_a_required_field(field: str) -> None:
    payload = manifest_payload()
    del payload[field]
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="missing",
        loc=(field,),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    INFRASTRUCTURE_ESCAPE_HATCHES,
    ids=[field for field, _value in INFRASTRUCTURE_ESCAPE_HATCHES],
)
def test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns(
    field: str,
    value: object,
) -> None:
    assert field not in RunManifest.model_fields
    payload = manifest_payload()
    payload[field] = value
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="extra_forbidden",
        loc=(field,),
    )


def test_manifest_rejects_empty_command() -> None:
    payload = manifest_payload()
    payload["command"] = []
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
        loc=("command",),
    )


#: The command as it reached AWS Batch on 2026-07-30, read back out of the intent record for
#: run_019fb4ce-cf24-7028-8eed-a32a28ec2493. A pilot user's shell quoting survived into the
#: form field, so ``shlex.split`` saw one fully quoted string and returned one token.
UNSPLIT_COMMAND_LINE = 'python -c "print(\\"hello from a second person\\")"'

PROGRAM_NAMES_NOTHING_CAN_EXECUTE: tuple[tuple[str, str], ...] = (
    ("a whole command line", UNSPLIT_COMMAND_LINE),
    ("a quoted program name", "'python'"),
    ("a double-quoted program name", '"python"'),
    ("a name padded with a space", "python "),
    ("a tab", "\tpython"),
    ("nothing at all", ""),
)


@pytest.mark.parametrize(
    ("description", "program"),
    PROGRAM_NAMES_NOTHING_CAN_EXECUTE,
    ids=[description for description, _program in PROGRAM_NAMES_NOTHING_CAN_EXECUTE],
)
def test_a_first_element_that_cannot_name_a_program_is_refused(
    description: str,
    program: str,
) -> None:
    payload = manifest_payload()
    payload["command"] = [program, "-m", "train"]
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("command",),
        message_fragment="the first command element must name a program",
    )


def test_the_refusal_says_the_command_line_was_never_split() -> None:
    payload = manifest_payload()
    payload["command"] = [UNSPLIT_COMMAND_LINE]
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    # The submitter reads this in the compile job's log and has to work out what to change,
    # so it names the cause rather than restating the rule. Batch's own message for this --
    # `executable file not found in $PATH` quoting the entire line -- arrives after a lead
    # has approved the run and an instance has pulled the image.
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("command",),
        message_fragment="was not split into arguments",
    )


def test_an_argument_may_hold_the_whitespace_and_quotes_a_program_name_may_not() -> None:
    payload = manifest_payload()
    # The corrected form of the same submission. Everything the rule forbids in the first
    # element is ordinary in the ones after it, which is why the rule reaches only the first.
    payload["command"] = ["python", "-c", "print('hello from a second person')"]
    manifest = RunManifest.model_validate(payload)
    assert manifest.command == ("python", "-c", "print('hello from a second person')")


#: The submission of 2026-08-01, split the way the runner split it. The guide prints single
#: quotes around the program and this one lost them, which is the opposite of the mistake
#: UNSPLIT_COMMAND_LINE records and reaches an instance just as easily.
COMMAND_THAT_LOST_ITS_QUOTES = [
    "bash",
    "-lc",
    "python",
    ".edullm/train_on_corpus.py",
    "$EDULLM_RUN_ID",
    "--steps",
    "20",
]


def test_a_shell_handed_more_than_one_word_after_dash_c_is_refused() -> None:
    payload = manifest_payload()
    payload["command"] = COMMAND_THAT_LOST_ITS_QUOTES
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("command",),
        message_fragment="reads exactly one word as the command",
    )


def test_the_refusal_prints_the_command_the_submitter_meant_to_send() -> None:
    """Mutation: state the rule instead of quoting the fix.

    What reached a GPU was a working command with its quotes dropped, so the correction is
    mechanical and the submitter should be able to copy it rather than derive it. The version
    that ran instead started a Python with nothing to interpret, exited 1 in under five
    seconds, and left its only explanation in a log stream the deploy credential is
    deliberately not allowed to read.
    """
    payload = manifest_payload()
    payload["command"] = COMMAND_THAT_LOST_ITS_QUOTES
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    message = str(exc_info.value)

    assert "bash -lc 'python .edullm/train_on_corpus.py $EDULLM_RUN_ID --steps 20'" in message


def test_the_quoted_form_the_guide_prints_is_accepted() -> None:
    payload = manifest_payload()
    # One word after -lc, which is the whole point. This is the line guides/olmo-core.md
    # prints, and a rule that refused it would be worse than the bug.
    payload["command"] = ["bash", "-lc", 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID"']
    manifest = RunManifest.model_validate(payload)
    assert manifest.command[2].startswith("python .edullm")


def test_a_shell_may_still_be_given_positional_arguments_after_a_real_command_line() -> None:
    """Mutation: refuse every case of more than one word after ``-c``.

    A shell reads the words after the command string as ``$0`` onward, and that is a real
    thing to do. The discriminator is not how many words follow but whether the first of them
    holds a command line: quoting that was lost leaves a bare program name there.
    """
    payload = manifest_payload()
    payload["command"] = ["bash", "-c", 'echo "$0 $1"', "first", "second"]
    manifest = RunManifest.model_validate(payload)
    assert manifest.command[-2:] == ("first", "second")


def test_a_program_that_is_not_a_shell_keeps_every_argument_it_was_given() -> None:
    # `python -c` takes one word too, but it takes it correctly here and the trailing words
    # are argv. The rule is about shells because a shell is what the platform tells people to
    # wrap their command in.
    payload = manifest_payload()
    payload["command"] = ["python", "-c", "import sys; print(sys.argv)", "one", "two"]
    manifest = RunManifest.model_validate(payload)
    assert manifest.command[-2:] == ("one", "two")


def test_a_program_named_by_absolute_path_is_still_a_program() -> None:
    payload = manifest_payload()
    payload["command"] = ["/usr/local/bin/python", "-m", "train"]
    manifest = RunManifest.model_validate(payload)
    assert manifest.command[0] == "/usr/local/bin/python"


def test_an_empty_command_is_still_refused_for_being_empty() -> None:
    # The program-name rule must not swallow this one: an absent command and an unusable
    # program name are different mistakes and a submitter fixes them differently.
    payload = manifest_payload()
    payload["command"] = []
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="too_short",
        loc=("command",),
    )


def test_manifest_rejects_non_decimal_runtime_hours() -> None:
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = 6
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="value_error",
        loc=("maximum_runtime_hours",),
        message_fragment="decimal values must be non-negative base-10 strings",
    )


def test_manifest_rejects_zero_runtime_hours() -> None:
    payload = manifest_payload()
    payload["maximum_runtime_hours"] = "0"
    with pytest.raises(ValidationError) as exc_info:
        RunManifest.model_validate(payload)
    assert_validation_error(
        exc_info.value,
        error_type="greater_than",
        loc=("maximum_runtime_hours",),
    )


def test_manifest_allows_single_attempt_without_checkpoint() -> None:
    payload = manifest_payload()
    payload["maximum_attempts"] = 1
    payload["checkpoint"] = None
    manifest = RunManifest.model_validate(payload)
    assert manifest.checkpoint is None
    assert manifest.maximum_attempts == 1


def test_every_manifest_fixture_has_reviewed_cost_expectation() -> None:
    assert set(REPRESENTATIVE_MANIFEST_FILENAMES) == set(REPRESENTATIVE_MANIFEST_COSTS)


def test_every_reviewed_manifest_is_also_required_by_the_gate() -> None:
    assert REQUIRED_REPRESENTATIVE_MANIFESTS == set(REPRESENTATIVE_MANIFEST_COSTS)


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_validates(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    assert manifest.schema_version == 1


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_profiles_are_registered(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    assert is_compute_profile_registered(manifest, catalog), (
        f"{filename}: compute profile {manifest.compute_profile!r} is not in the catalog"
    )
    assert is_workload_profile_registered(manifest, catalog), (
        f"{filename}: workload profile {manifest.workload_profile!r} is not in the catalog"
    )


@pytest.mark.parametrize(
    ("filename", "expected_cost_usd"),
    list(REPRESENTATIVE_MANIFEST_COSTS.items()),
    ids=list(REPRESENTATIVE_MANIFEST_COSTS.keys()),
)
def test_representative_manifest_maximum_cost(
    filename: str,
    expected_cost_usd: Decimal,
) -> None:
    manifest = load_representative_manifest(filename)
    catalog = load_workload_catalog()
    assert compute_manifest_maximum_cost(manifest, catalog) == expected_cost_usd


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_compiles_identically_on_every_load(filename: str) -> None:
    first = load_representative_manifest(filename)
    second = load_representative_manifest(filename)
    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert sha256_digest(first) == sha256_digest(second)


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_digest_covers_its_canonical_encoding(filename: str) -> None:
    manifest = load_representative_manifest(filename)
    encoded = canonical_json_bytes(manifest)
    restored = RunManifest.model_validate(json.loads(encoded))
    assert restored == manifest
    assert sha256_digest(manifest) == f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_source_field_order_does_not_change_a_fixture_digest(filename: str) -> None:
    document = load_manifest_document(filename)
    reordered = reverse_mapping_order(document)
    assert list(reordered) != list(document)
    assert sha256_digest(RunManifest.model_validate(reordered)) == sha256_digest(
        load_representative_manifest(filename)
    )


@pytest.mark.parametrize("filename", REPRESENTATIVE_MANIFEST_FILENAMES)
def test_representative_manifest_records_a_non_runnable_identity(filename: str) -> None:
    source = (MANIFEST_FIXTURES_DIR / filename).read_text(encoding="utf-8")
    assert "NON-RUNNABLE FIXTURE IDENTITY" in source


def test_no_manifest_field_names_an_execution_backend() -> None:
    named_backends = [
        field
        for field in RunManifest.model_fields
        if any(token in field.casefold() for token in BACKEND_TOKENS)
    ]
    assert named_backends == []


def test_sagemaker_and_ec2_training_requests_have_identical_field_sets() -> None:
    ec2 = load_representative_manifest(EC2_TRAINING_MANIFEST)
    sagemaker = load_representative_manifest(SAGEMAKER_TRAINING_MANIFEST)
    assert set(sagemaker.model_dump()) == set(ec2.model_dump()) == set(RunManifest.model_fields)
    assert set(load_manifest_document(SAGEMAKER_TRAINING_MANIFEST)) == set(
        load_manifest_document(EC2_TRAINING_MANIFEST)
    )


def test_a_sagemaker_request_differs_from_its_ec2_twin_only_in_the_profile_it_names() -> None:
    ec2 = load_representative_manifest(EC2_TRAINING_MANIFEST)
    sagemaker = load_representative_manifest(SAGEMAKER_TRAINING_MANIFEST)
    assert differing_fields(ec2, sagemaker) == {"compute_profile"}
    assert sagemaker.compute_profile != ec2.compute_profile
    assert sha256_digest(sagemaker) != sha256_digest(ec2)


def test_the_branch_experiment_is_identified_by_its_commit_not_by_its_repository() -> None:
    branch = load_representative_manifest(BRANCH_EXPERIMENT_MANIFEST)
    other = load_representative_manifest(EC2_TRAINING_MANIFEST)
    assert branch.repository == other.repository
    assert branch.team != other.team
    assert branch.commit_sha != other.commit_sha
    assert sha256_digest(branch) != sha256_digest(other)


def test_two_experiments_differing_only_in_commit_have_different_digests() -> None:
    branch = load_representative_manifest(BRANCH_EXPERIMENT_MANIFEST)
    rebased = branch.model_copy(update={"commit_sha": "f" * 40})
    assert differing_fields(branch, rebased) == {"commit_sha"}
    assert sha256_digest(branch) != sha256_digest(rebased)


def test_a_shared_repository_does_not_identify_the_experiment_running_in_it() -> None:
    shared = [
        load_representative_manifest(filename)
        for filename in REPRESENTATIVE_MANIFEST_FILENAMES
        if load_representative_manifest(filename).repository == "OLMo-core"
    ]
    assert len(shared) > 2
    assert len({manifest.team for manifest in shared}) > 1
    assert len({sha256_digest(manifest) for manifest in shared}) == len(shared)
