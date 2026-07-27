from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import (
    COMMIT_SHA_PATTERN,
    IMAGE_DIGEST_PATTERN,
    RunManifest,
)
from edullm_platform.contracts.policy import RequestFacts
from edullm_platform.contracts.workload import (
    CostInputs,
    UnregisteredComputeProfileError,
    WorkloadCatalog,
)

COMMIT_SHA_REGEX = re.compile(COMMIT_SHA_PATTERN)
IMAGE_DIGEST_REGEX = re.compile(IMAGE_DIGEST_PATTERN)

REPRESENTATIVE_MANIFEST_COSTS: Final = {
    "cpu-routine.yaml": Decimal("2.86"),
    "gpu-routine.yaml": Decimal("5.67"),
    "gpu-exception.yaml": Decimal("73.74"),
    "olmo-branch-routine.yaml": Decimal("6.04"),
    "sagemaker-routine.yaml": Decimal("1.52"),
    "multiseed-routine.yaml": Decimal("20.12"),
}


def manifest_has_immutable_revision(manifest: RunManifest) -> bool:
    return COMMIT_SHA_REGEX.fullmatch(manifest.commit_sha) is not None


def manifest_has_immutable_image(manifest: RunManifest) -> bool:
    return IMAGE_DIGEST_REGEX.fullmatch(manifest.image_digest) is not None


def is_compute_profile_registered(manifest: RunManifest, catalog: WorkloadCatalog) -> bool:
    registered_names = {profile.name for profile in catalog.compute_profiles}
    return manifest.compute_profile in registered_names


def is_workload_profile_registered(manifest: RunManifest, catalog: WorkloadCatalog) -> bool:
    registered_names = {workload.name for workload in catalog.workloads}
    return manifest.workload_profile in registered_names


def manifest_fanout_size(manifest: RunManifest) -> int:
    return 1 if manifest.fanout is None else manifest.fanout.size


def manifest_fanout_parallelism(manifest: RunManifest) -> int:
    return 1 if manifest.fanout is None else manifest.fanout.max_parallel


def compute_manifest_cost_inputs(
    manifest: RunManifest, catalog: WorkloadCatalog
) -> CostInputs:
    """The worst-case cost of a submission, with the arithmetic that produced it.

    Kept separate from :func:`compute_manifest_maximum_cost` because two callers want
    different things from the same calculation. Classification needs only the total. An
    approver needs the factors, because a bare dollar figure invites a rubber stamp while
    ``rate x nodes x hours x attempts x cells`` shows which of them is the large one. A
    decision record needs the factors for a third reason: without them a later reading
    cannot tell an underestimate from a policy change.
    """
    if not is_compute_profile_registered(manifest, catalog):
        raise UnregisteredComputeProfileError(
            f"unregistered compute profile: {manifest.compute_profile!r}"
        )
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    profile = profile_by_name[manifest.compute_profile]
    return CostInputs(
        hourly_rate_usd=profile.hourly_rate_usd,
        nodes=profile.nodes,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
        cells=manifest_fanout_size(manifest),
    )


def compute_manifest_maximum_cost(manifest: RunManifest, catalog: WorkloadCatalog) -> Decimal:
    return compute_manifest_cost_inputs(manifest, catalog).maximum_compute_cost_usd


def build_request_facts(
    manifest: RunManifest,
    *,
    inventory: OrganizationInventory,
    catalog: WorkloadCatalog,
    dataset_registry: DatasetRegistry,
    estimated_cost_usd: Decimal,
) -> RequestFacts:
    """Derive the facts policy classifies, from the manifest and reviewed configuration.

    Every field here is derived rather than accepted. A submitter supplies the manifest and
    can therefore choose values that make their request expensive, over a ceiling, or
    unregistered — all of which push the classification toward ``exception`` or outright
    denial. What a submitter cannot do is supply a fact: the registration flags are
    answered by configuration, the cost is recomputed from the catalog's rate, and there is
    no input that says "this is routine". That asymmetry is what makes classification a
    boundary rather than a formality, and it is why this function takes the registry as an
    argument instead of reading a set defined next to a caller.
    """
    return RequestFacts(
        claimed_team=manifest.team,
        repository_registered=manifest.repository in inventory.pilot_repositories,
        dataset_registered=dataset_registry.is_registered(manifest.dataset_release),
        compute_profile_registered=is_compute_profile_registered(manifest, catalog),
        immutable_revision=manifest_has_immutable_revision(manifest),
        immutable_image=manifest_has_immutable_image(manifest),
        estimated_cost_usd=estimated_cost_usd,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
        fanout_size=manifest_fanout_size(manifest),
        fanout_parallelism=manifest_fanout_parallelism(manifest),
    )


def load_manifest(path: Path) -> RunManifest:
    return load_yaml(path, RunManifest)


def load_manifests_from_directory(manifest_dir: Path) -> tuple[tuple[str, RunManifest], ...]:
    paths = sorted(manifest_dir.glob("*.yaml"))
    return tuple((path.name, load_manifest(path)) for path in paths)
