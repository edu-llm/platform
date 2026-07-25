from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from edullm_platform.config import load_yaml
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.workload import CostInputs, WorkloadCatalog

COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def manifest_has_immutable_revision(manifest: RunManifest) -> bool:
    return COMMIT_SHA_PATTERN.fullmatch(manifest.commit_sha) is not None


def manifest_has_immutable_image(manifest: RunManifest) -> bool:
    return IMAGE_DIGEST_PATTERN.fullmatch(manifest.image_digest) is not None


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
