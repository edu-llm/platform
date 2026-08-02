from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanPolicy,
    ImageScanSummary,
    ScanFinding,
    image_scan_is_reviewed,
)
from edullm_platform.contracts.manifest import (
    COMMIT_SHA_PATTERN,
    IMAGE_DIGEST_PATTERN,
    RunManifest,
)
from edullm_platform.contracts.policy import RequestFacts
from edullm_platform.contracts.repository_registry import RepositoryRegistry
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
    # Was 73.74, which was thirteen hours of gpu-4xa10g against a routine runtime ceiling of
    # twelve. config/policy.yaml sets that ceiling to 24 now, so the fixture that exists to
    # be an exception on runtime alone had to move to twenty-five hours to go on being one.
    "gpu-exception.yaml": Decimal("141.80"),
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
    repositories: RepositoryRegistry,
    catalog: WorkloadCatalog,
    dataset_registry: DatasetRegistry,
    estimated_cost_usd: Decimal,
    image_scan_policy: ImageScanPolicy | None = None,
    image_scan_registry: ImageScanExceptionRegistry | None = None,
    image_scan_summary: ImageScanSummary | None = None,
    image_scan_findings: Sequence[ScanFinding] | None = None,
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

    **``repository_registered`` reads the repository registry, and used to read the roster.**
    It was ``manifest.repository in inventory.pilot_repositories``, and
    ``config/organization.yaml`` sets that to ``OLMo-core`` and ``dolma``. Those two lists
    are not the same question. ``pilot_repositories`` is a declaration of what the pilot
    programme covers, which is why the Phase 0 inventory check holds it to exactly those
    two; ``config/repositories.yaml`` is where a repository acquires an ECR repository, a
    registered base image and a Dockerfile path, which is what "can be built and run here"
    consists of.

    Reading the wrong one was wrong in both directions at once, and a probe confirmed the
    expensive direction: ``repository: dolma`` with ``workload_profile:
    dolma-tokenize-smoke`` was *accepted*, routed to a lead, and would have been submitted
    to the CPU queue -- where it would have run the OLMo-core image, because the image is
    pinned in the job definition rather than chosen by the submission. The other direction
    is quieter and would have arrived next: ``edullm-data`` is registered, has a published
    image, and the first workload written against it would have been denied outright as an
    unregistered repository.

    **The three image-scan arguments and what their absence means.**
    ``image_scan_summary=None`` means "no scan result is in hand", which resolves to
    ``image_scan_reviewed=False`` unless a recorded exception covers the digest. That is the
    fail-closed direction: an image nobody has scanned is not an image somebody has cleared.
    The summary is an argument rather than something read here because the two production
    callers get it from different places -- the compile step from the provenance record,
    admission from ECR -- and admission re-deriving it is what stops the compile step's
    answer from being taken on trust.

    ``image_scan_policy=None`` is different and is a deliberate opt-out: it means this
    caller is not evaluating the scan gate at all, and the fact is reported as reviewed.
    That exists for the Phase 0 fixture path, which compiles manifests naming digests that
    were never published and so can have no scan. It is a fail-open default, which is why
    it has to be asked for by omission rather than arrived at: both production callers pass
    ``policy.image_scan``, and
    ``tests/test_phase3_image_scan.py::test_both_production_callers_evaluate_the_scan_gate``
    fails if either stops. Without that test this argument would be the quiet way to turn
    the gate off.
    """
    if image_scan_policy is None:
        image_scan_reviewed = True
    else:
        image_scan_reviewed = image_scan_is_reviewed(
            image_digest=manifest.image_digest,
            summary=image_scan_summary,
            blocking_findings=image_scan_findings,
            policy=image_scan_policy,
            registry=image_scan_registry or ImageScanExceptionRegistry(schema_version=1),
        )
    return RequestFacts(
        claimed_team=manifest.team,
        repository_registered=repositories.is_registered(manifest.repository),
        dataset_registered=dataset_registry.is_registered(manifest.dataset_release),
        compute_profile_registered=is_compute_profile_registered(manifest, catalog),
        immutable_revision=manifest_has_immutable_revision(manifest),
        immutable_image=manifest_has_immutable_image(manifest),
        image_scan_reviewed=image_scan_reviewed,
        estimated_cost_usd=estimated_cost_usd,
        maximum_runtime_hours=manifest.maximum_runtime_hours,
        maximum_attempts=manifest.maximum_attempts,
        fanout_size=manifest_fanout_size(manifest),
        # fanout_parallelism IS LEFT AT ITS DEFAULT OF 1 AND NO LONGER HAS A SOURCE.
        #
        # It used to read RunManifest.fanout.max_parallel, which was removed because Batch
        # cannot apply a concurrency cap and the field therefore recorded a control that
        # did not exist. Nothing else in a manifest describes concurrency, so there is
        # nothing to put here.
        #
        # The consequence is deliberate and belongs to whoever owns config/policy.yaml
        # rather than to this line. RequestFacts.fanout_parallelism and
        # PolicyThresholds.routine_maximum_parallelism both still exist and
        # classify_request still compares them, so the bound is live machinery that no
        # submission can now trip. A request that was an exception on parallelism alone --
        # thirty-two cells declaring sixteen at once -- is routine from here on. Removing
        # the threshold would be the tidier tree and it is not this change to make. It
        # loosens who may release a run, and the file's own rule is that a thresholds edit
        # is a policy_version bump somebody reviews rather than a refactor that follows a
        # field around.
    )


def load_manifest(path: Path) -> RunManifest:
    return load_yaml(path, RunManifest)


def load_manifests_from_directory(manifest_dir: Path) -> tuple[tuple[str, RunManifest], ...]:
    paths = sorted(manifest_dir.glob("*.yaml"))
    return tuple((path.name, load_manifest(path)) for path in paths)
