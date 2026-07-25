from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.manifest import (
    COMMIT_SHA_PATTERN,
    IMAGE_DIGEST_PATTERN,
    RunManifest,
)
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog

COMMIT_SHA_REGEX = re.compile(COMMIT_SHA_PATTERN)
IMAGE_DIGEST_REGEX = re.compile(IMAGE_DIGEST_PATTERN)

REPRESENTATIVE_MANIFEST_COSTS: Final = {
    "cpu-routine.yaml": Decimal("2.86"),
    "gpu-routine.yaml": Decimal("5.67"),
    "gpu-exception.yaml": Decimal("73.74"),
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


def compute_manifest_maximum_cost(manifest: RunManifest, catalog: WorkloadCatalog) -> Decimal:
    if not is_compute_profile_registered(manifest, catalog):
        raise ValueError(f"unregistered compute profile: {manifest.compute_profile!r}")
    profile_by_name = {profile.name: profile for profile in catalog.compute_profiles}
    profile = profile_by_name[manifest.compute_profile]
    return CostInputs(
        hourly_rate_usd=profile.hourly_rate_usd,
        nodes=profile.nodes,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
    ).maximum_compute_cost_usd


def load_manifest(path: Path) -> RunManifest:
    return load_yaml(path, RunManifest)


def load_manifests_from_directory(manifest_dir: Path) -> tuple[tuple[str, RunManifest], ...]:
    paths = sorted(manifest_dir.glob("*.yaml"))
    return tuple((path.name, load_manifest(path)) for path in paths)
