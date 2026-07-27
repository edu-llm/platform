import json
from pathlib import Path

from pydantic import BaseModel

from edullm_platform.contracts.admission import DecisionRecord, IntentRecord
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.submission import SubmissionInputs

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "organization.schema.json": OrganizationInventory,
    "workload-catalog.schema.json": WorkloadCatalog,
    "policy.schema.json": ApprovalPolicy,
    "repositories.schema.json": RepositoryRegistry,
    "run-manifest.schema.json": RunManifest,
    "datasets.schema.json": DatasetRegistry,
    # The two records the lineage store holds. Exported for the same reason the
    # configuration schemas are: an immutable store is read by things that were not built
    # alongside it, and a published shape is the only way a later reader can tell a record
    # this platform wrote from one it did not.
    "intent-record.schema.json": IntentRecord,
    "decision-record.schema.json": DecisionRecord,
    "submission-inputs.schema.json": SubmissionInputs,
}


def render_schema(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"


def rendered_schemas() -> dict[str, str]:
    return {filename: render_schema(model) for filename, model in SCHEMA_MODELS.items()}


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    schemas_dir = project_root / "schemas"
    schemas_dir.mkdir(exist_ok=True)
    for filename, content in rendered_schemas().items():
        (schemas_dir / filename).write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
