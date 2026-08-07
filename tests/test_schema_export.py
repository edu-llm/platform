import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from pydantic import BaseModel, ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalPolicy, PolicyThresholds
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog, WorkloadProfile
from tools.export_schemas import rendered_schemas


def assert_validation_error(
    error: ValidationError,
    *,
    error_type: str,
    message_fragment: str | None = None,
    loc: tuple[object, ...] | None = None,
) -> None:
    matching_errors = [item for item in error.errors() if item["type"] == error_type]
    assert matching_errors, f"expected error type {error_type!r}, got {error.errors()}"
    if loc is not None:
        assert any(item["loc"] == loc for item in matching_errors), (
            f"expected loc {loc!r} in {error_type!r} errors, got {[item['loc'] for item in matching_errors]}"
        )
    if message_fragment is not None:
        assert any(message_fragment in item["msg"] for item in matching_errors), (
            f"expected {message_fragment!r} in {error_type!r} messages, "
            f"got {[item['msg'] for item in matching_errors]}"
        )


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


POLICY_THRESHOLDS_PAYLOAD: dict[str, object] = {"automatic_below_cost_usd": "500"}

COMPUTE_PROFILE_PAYLOAD: dict[str, object] = {
    "name": "cpu-test",
    "instance_type": "c7i.8xlarge",
    "accelerator": "cpu",
    "nodes": 1,
    "hourly_rate_usd": "1.428",
    "pricing_source": "test",
    "pricing_observed_at": "2026-07-24",
    "provisioned": False,
}

WORKLOAD_PROFILE_PAYLOAD: dict[str, object] = {
    "name": "smoke",
    "repository": "dolma",
    "maximum_runtime_hours": "1",
    "maximum_attempts": 1,
    "checkpoint": None,
}

RUN_MANIFEST_PAYLOAD: dict[str, object] = {
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

REGISTERED_REPOSITORY_PAYLOAD: dict[str, object] = {
    "repository": "OLMo-core",
    "github_repository_id": 1306868157,
    "default_branch": "main",
    "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
    "base_image_repository": "docker.io/library/python",
    "base_image_digest": "sha256:" + "a" * 64,
    "dockerfile_path": ".edullm/Dockerfile",
    "build_context": ".",
}

# POLICY CONTRIBUTES ONE ROW WHERE IT CONTRIBUTED TWO. ``routine_maximum_cost_usd`` was the
# non-negative one and ``routine_maximum_runtime_hours`` the positive one, and v5 retired
# both along with the other four ceilings. What is left is the single automatic bound, which
# is positive. The non-negative alias is still exercised by ``RequestFacts`` and by the
# manifest, so no probe loses its subject.
EXPORTED_DECIMAL_FIELDS: tuple[tuple[str, type[BaseModel], str, bool, dict[str, object]], ...] = (
    (
        "policy-automatic-bound",
        PolicyThresholds,
        "automatic_below_cost_usd",
        True,
        POLICY_THRESHOLDS_PAYLOAD,
    ),
    ("catalog-rate", ComputeProfile, "hourly_rate_usd", True, COMPUTE_PROFILE_PAYLOAD),
    (
        "catalog-runtime",
        WorkloadProfile,
        "maximum_runtime_hours",
        True,
        WORKLOAD_PROFILE_PAYLOAD,
    ),
    ("manifest-runtime", RunManifest, "maximum_runtime_hours", True, RUN_MANIFEST_PAYLOAD),
)

#: One probe per decision the two decimal aliases encode, and nothing else.
#:
#: Each of these was the only probe to catch at least one deliberate regression; seven
#: others were removed for catching nothing a survivor did not. ``"0"`` is the whole of
#: what separates ``ge=0`` from ``gt=0``, and the field-level constraint is read by
#: nothing else here. ``"0.001"`` is the positive pattern's second branch, without which
#: it could be written ``[1-9][0-9]*`` and silently refuse every rate below a dollar.
#: ``"1.428"`` is the fraction. ``"01"`` and ``"-1"`` are the two things ``Decimal``
#: accepts and neither pattern may, and ``"-1"`` is also the only probe that reads the
#: sign. ``"500"`` and the integer ``500`` are one value on either side of the wire
#: format: the schema says ``type: string``, and what the validator does with a number
#: has to agree with that.
SHARED_DECIMAL_PROBES: tuple[object, ...] = (
    "0",
    "0.001",
    "1.428",
    "500",
    "01",
    "-1",
    500,
)


def decimal_probe_should_accept(value: object, *, positive_only: bool) -> bool:
    if positive_only:
        return value in {"0.001", "1.428", "500"}
    return value in {"0", "0.001", "1.428", "500"}


def test_checked_in_schemas_match_contract_models() -> None:
    schemas_dir = project_root() / "schemas"
    for filename, expected in rendered_schemas().items():
        assert (schemas_dir / filename).read_text(encoding="utf-8") == expected


def test_rendered_schemas_cover_all_root_contract_models() -> None:
    assert set(rendered_schemas()) == {
        "organization.schema.json",
        "workload-catalog.schema.json",
        "policy.schema.json",
        "repositories.schema.json",
        "run-manifest.schema.json",
        "datasets.schema.json",
        # The submission form and the two records the lineage store holds. Exported for a
        # reason the configuration schemas do not share: an immutable store is read by
        # things that were not built alongside it, so a published shape is how a later
        # reader tells a record this platform wrote from one it did not.
        "submission-inputs.schema.json",
        "intent-record.schema.json",
        "decision-record.schema.json",
        # Reviewed configuration a human edits: which published digests somebody has read
        # the scan findings for and accepted.
        "image-exceptions.schema.json",
        # Reviewed configuration a tool writes and a human reviews: which tokenizers each
        # published training image was measured to hold. Exported because it decides the one
        # verdict in this tree that can lose somebody a GPU allocation, so a malformed entry
        # has to fail at load rather than at the point `edullm data` says a corpus will run.
        "image-contents.schema.json",
        # The execution records Phase 3 writes into the same store. Defined in Phase 0 and
        # constructed by nothing until Phase 3, which is why they arrive here only now.
        "logical-run.schema.json",
        "scheduler-attempt.schema.json",
        "lifecycle-event.schema.json",
        "checkpoint-manifest.schema.json",
        "result-manifest.schema.json",
        # The one record Phase 3 adds rather than reuses: what Batch said when it accepted
        # a submission, which is a fact no Phase 0 contract could hold because every one of
        # them needs an outcome that does not exist yet.
        "batch-job-binding.schema.json",
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("base_image_repository", "docker.io/library/python:3.12"),
        (
            "base_image_repository",
            "docker.io/library/python@sha256:" + "a" * 64,
        ),
        ("dockerfile_path", ""),
        ("dockerfile_path", "."),
        ("dockerfile_path", "/Dockerfile"),
        ("dockerfile_path", "../Dockerfile"),
        ("dockerfile_path", r"images\Dockerfile"),
        ("build_context", ""),
        ("build_context", "/workspace"),
        ("build_context", "../workspace"),
        ("build_context", r"images\workspace"),
    ],
)
def test_repository_schema_rejects_runtime_invalid_image_and_path_values(
    field: str,
    invalid_value: str,
) -> None:
    schema = json.loads(rendered_schemas()["repositories.schema.json"])
    validator = jsonschema.Draft202012Validator(schema)
    repository = dict(REGISTERED_REPOSITORY_PAYLOAD)
    repository[field] = invalid_value

    assert not validator.is_valid({"repositories": [repository]})


def test_repository_schema_allows_dot_build_context() -> None:
    schema = json.loads(rendered_schemas()["repositories.schema.json"])
    validator = jsonschema.Draft202012Validator(schema)

    assert validator.is_valid({"repositories": [REGISTERED_REPOSITORY_PAYLOAD]})


def test_load_yaml_rejects_duplicate_mapping_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "duplicate-keys.yaml"
    config_path.write_text(
        "admins:\n  - alice\nadmins:\n  - bob\n",
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError) as exc_info:
        load_yaml(config_path, OrganizationInventory)
    assert "duplicate" in str(exc_info.value).lower()


def test_load_yaml_rejects_duplicate_key_in_nested_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "nested-duplicate-policy.yaml"
    config_path.write_text(
        (
            "thresholds:\n"
            "  routine_maximum_cost_usd: \"500\"\n"
            "  routine_maximum_cost_usd: \"600\"\n"
            "  routine_maximum_runtime_hours: \"12\"\n"
            "  routine_maximum_attempts: 2\n"
            "routine_approver_role: team_lead\n"
            "exception_approver_roles:\n"
            "  - platform_admin\n"
            "denied_outright:\n"
            "  - unregistered_repository\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError) as exc_info:
        load_yaml(config_path, ApprovalPolicy)
    assert "duplicate" in str(exc_info.value).lower()


def test_load_yaml_rejects_duplicate_key_in_sequence_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "sequence-mapping-duplicate.yaml"
    config_path.write_text(
        (
            "compute_profiles:\n"
            "  - name: cpu-test\n"
            "    accelerator: cpu\n"
            "    nodes: 1\n"
            "    hourly_rate_usd: \"1.428\"\n"
            "    hourly_rate_usd: \"9.999\"\n"
            "    pricing_source: test\n"
            "    pricing_observed_at: \"2026-07-24\"\n"
            "workloads:\n"
            "  - name: smoke\n"
            "    repository: dolma\n"
            "    compute_profile: cpu-test\n"
            "    maximum_runtime_hours: \"1\"\n"
            "    maximum_attempts: 1\n"
            "    checkpoint: null\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(yaml.YAMLError) as exc_info:
        load_yaml(config_path, WorkloadCatalog)
    assert "duplicate" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "document",
    [
        "",
        "null\n",
        "[]\n",
        "just-a-scalar\n",
    ],
    ids=["empty", "null", "list", "scalar"],
)
def test_load_yaml_rejects_non_mapping_top_level_documents(
    tmp_path: Path,
    document: str,
) -> None:
    config_path = tmp_path / "invalid-root.yaml"
    config_path.write_text(document, encoding="utf-8")
    with pytest.raises(TypeError) as exc_info:
        load_yaml(config_path, OrganizationInventory)
    assert "top-level mapping" in str(exc_info.value).lower()
    assert str(config_path) in str(exc_info.value)


def test_load_yaml_preserves_pydantic_validation_error(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid-inventory.yaml"
    config_path.write_text(
        "admins: []\nteam_leads: []\nmembers: []\npilot_repositories: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError) as exc_info:
        load_yaml(config_path, OrganizationInventory)
    assert_validation_error(exc_info.value, error_type="too_short", loc=("members",))


@pytest.mark.parametrize(
    ("case_id", "model_type", "field_name", "positive_only", "base_payload"),
    EXPORTED_DECIMAL_FIELDS,
)
@pytest.mark.parametrize("value", SHARED_DECIMAL_PROBES)
def test_exported_decimal_schema_matches_model_validation(
    case_id: str,
    model_type: type[BaseModel],
    field_name: str,
    positive_only: bool,
    base_payload: dict[str, object],
    value: object,
) -> None:
    """The published shape of a decimal and the validator that reads one agree.

    Mutation: move one without the other. ``StrictDecimal`` and ``PositiveStrictDecimal``
    carry their schema as a literal ``WithJsonSchema`` dict and their behaviour as
    ``BeforeValidator(parse_decimal)`` plus a ``ge`` or ``gt`` written at each field.
    Neither is derived from the other, so nothing but this holds the two together.

    **This is not the byte comparison, and the two are easy to mistake for each other.**
    ``tools/export_schemas.py`` followed by ``git diff --exit-code schemas`` says that the
    committed file is what the models render today. It says nothing about what the models
    accept, so it is green while a published schema and the validator behind it disagree.
    Six regressions were tried that neither it nor
    ``test_exported_decimal_schemas_use_distinct_non_negative_and_positive_patterns``
    notices at all: dropping ``Field(gt=0)`` from each of the four positive fields in
    turn, letting ``parse_decimal`` take any string ``Decimal`` will parse, and letting it
    take an integer. This catches five of the six.

    The sixth is ``Field(ge=0)`` on the cost threshold, which no probe here can reach:
    every string the non-negative pattern admits is already non-negative, so that
    constraint only guards a ``Decimal`` built in Python, which is not a JSON value and
    has no schema side to disagree with. ``gt=0`` is reachable only because zero is
    spellable as a string both patterns' validator accepts.
    """
    field_schema = model_type.model_json_schema()["properties"][field_name]
    validator = jsonschema.Draft202012Validator(field_schema)
    schema_accepts = validator.is_valid(value)

    payload = dict(base_payload)
    payload[field_name] = value
    try:
        model_type.model_validate(payload)
        model_accepts = True
    except ValidationError:
        model_accepts = False

    should_accept = decimal_probe_should_accept(value, positive_only=positive_only)
    assert model_accepts is should_accept, (
        f"{case_id} model verdict mismatch for {value!r}: "
        f"expected {'accept' if should_accept else 'reject'}, got model={model_accepts}"
    )
    assert schema_accepts is should_accept, (
        f"{case_id} schema verdict mismatch for {value!r}: "
        f"expected {'accept' if should_accept else 'reject'}, got schema={schema_accepts}"
    )


def test_exported_decimal_schemas_use_distinct_non_negative_and_positive_patterns() -> None:
    policy_schema = json.loads(
        (project_root() / "schemas" / "policy.schema.json").read_text(encoding="utf-8")
    )
    catalog_schema = json.loads(
        (project_root() / "schemas" / "workload-catalog.schema.json").read_text(encoding="utf-8")
    )
    manifest_schema = json.loads(
        (project_root() / "schemas" / "run-manifest.schema.json").read_text(encoding="utf-8")
    )
    decision_schema = json.loads(
        (project_root() / "schemas" / "decision-record.schema.json").read_text(encoding="utf-8")
    )
    non_negative_pattern = "^(0|[1-9][0-9]*)(\\.[0-9]+)?$"
    positive_pattern = "^(?:[1-9][0-9]*(?:\\.[0-9]+)?|0\\.[0-9]*[1-9][0-9]*)$"

    # POLICY PUBLISHES ONE DECIMAL NOW AND IT IS THE POSITIVE ONE. It published two, and the
    # non-negative one was ``routine_maximum_cost_usd``, which v5 retired. The two patterns
    # are still asserted against each other here, one schema apart: a cost of zero is a real
    # answer for a run that never started, and a bound of zero is not a bound.
    bound_schema = policy_schema["$defs"]["PolicyThresholds"]["properties"][
        "automatic_below_cost_usd"
    ]
    recorded_runtime_schema = decision_schema["$defs"]["CostInputs"]["properties"][
        "maximum_runtime_hours"
    ]
    assert bound_schema["type"] == "string"
    assert bound_schema["pattern"] == positive_pattern
    assert recorded_runtime_schema["type"] == "string"
    assert recorded_runtime_schema["pattern"] == non_negative_pattern
    assert non_negative_pattern != positive_pattern

    rate_schema = catalog_schema["$defs"]["ComputeProfile"]["properties"]["hourly_rate_usd"]
    workload_runtime_schema = catalog_schema["$defs"]["WorkloadProfile"]["properties"][
        "maximum_runtime_hours"
    ]
    assert rate_schema["pattern"] == positive_pattern
    assert workload_runtime_schema["pattern"] == positive_pattern

    manifest_runtime_schema = manifest_schema["properties"]["maximum_runtime_hours"]
    assert manifest_runtime_schema["pattern"] == positive_pattern

