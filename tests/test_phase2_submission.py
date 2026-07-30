import inspect
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.canonical import sha256_digest
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_scan import (
    ImageScanExceptionRegistry,
    ImageScanSummary,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import FanOut, RunManifest
from edullm_platform.contracts.policy import ApprovalClass, ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import WorkloadCatalog, WorkloadProfile
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.image_resolution import PublishedImage
from edullm_platform.submission import (
    CompiledSubmission,
    SubmissionInputs,
    compile_submission,
    render_approver_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: GitHub raised the workflow_dispatch input ceiling from ten to twenty-five in December
#: 2025, and a workflow that declares more fails schema validation rather than degrading.
WORKFLOW_DISPATCH_INPUT_CEILING = 25

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"
SUBMITTER = "caiiris"
REPOSITORY_URL = "https://github.com/edu-llm/dolma"

COMMIT_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64

#: A second image published from the same commit, which is what a rebuild leaves behind. A
#: single commit has been measured built four times in this project.
REBUILT_DIGEST = "sha256:" + "c" * 64

#: Well-formed, genuinely published, and not from ``COMMIT_SHA``. This is what the
#: surviving ``image_digest`` field can still carry, and the only thing standing in front
#: of it is the override check.
DIGEST_FROM_ANOTHER_COMMIT = "sha256:" + "d" * 64

FIRST_PUSH = datetime(2026, 7, 26, 9, 2, tzinfo=UTC)
SECOND_PUSH = datetime(2026, 7, 26, 18, 30, tzinfo=UTC)

#: The registered workload most of this module compiles against. It was
#: ``dolma-tokenize-smoke`` until ``repository_registered`` started reading
#: ``config/repositories.yaml`` rather than the roster's pilot list: dolma is a pilot
#: repository and has no registration, so a submission naming it is now denied outright and
#: cannot stand in for an ordinary one. ``DOLMA_WORKLOAD`` is kept below for the tests that
#: are about the catalog rather than about a submission that compiles.
CPU_WORKLOAD = "olmo-core-cpu-smoke"
DOLMA_WORKLOAD = "dolma-tokenize-smoke"
OLMO_WORKLOAD = "olmo-core-train-smoke"
REGISTERED_DATASET = "dolma-2026-07"
UNREGISTERED_DATASET = "dolma-2026-99"
UNREGISTERED_COMPUTE_PROFILE = "cpu-1024vcpu"

FANOUT_FIELDS: dict[str, object] = {
    "fanout_size": 4,
    "fanout_parallelism": 2,
    "fanout_index_parameter": "seed",
}
PARTIAL_FANOUTS = [
    declared
    for count in (1, 2)
    for declared in combinations(sorted(FANOUT_FIELDS), count)
]

REQUIRED_CONTEXT_FIELDS = (
    "classification",
    "compute profile and its rate",
    "cost arithmetic",
    "dataset release",
    "gate",
    "image digest",
    "linked commit",
    "manifest digest",
    "policy version",
    "repository",
    "submitter",
    "team",
    "workload profile",
)

#: One exception per routine ceiling, each over exactly the one it names.
EXCEEDED_CEILINGS: tuple[tuple[str, dict[str, object], str], ...] = (
    (
        "cost",
        {
            "maximum_runtime_hours": "12",
            "fanout_size": 10,
            "fanout_parallelism": 5,
            "fanout_index_parameter": "seed",
        },
        "worst-case cost $680.64 exceeds the routine ceiling of",
    ),
    (
        "runtime",
        {"maximum_runtime_hours": "13"},
        "runtime bound of 13h exceeds the routine ceiling of 12h",
    ),
    (
        "attempts",
        {"maximum_attempts": 3},
        "attempt bound of 3 exceeds the routine ceiling of 2",
    ),
    (
        "fan-out size",
        {"fanout_size": 65, "fanout_parallelism": 8, "fanout_index_parameter": "shard"},
        "fan-out size of 65 exceeds the routine ceiling of 64",
    ),
    (
        "fan-out parallelism",
        {
            "maximum_runtime_hours": "0.5",
            "fanout_size": 64,
            "fanout_parallelism": 9,
            "fanout_index_parameter": "shard",
        },
        "fan-out parallelism of 9 exceeds the routine ceiling of 8",
    ),
)

CEILING_IDS = [name for name, _form, _phrase in EXCEEDED_CEILINGS]


def load_organization_inventory() -> OrganizationInventory:
    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def load_approval_policy() -> ApprovalPolicy:
    return load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)


def load_workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def load_dataset_registry() -> DatasetRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)


def load_repository_registry() -> RepositoryRegistry:
    return load_yaml(PROJECT_ROOT / "config" / "repositories.yaml", RepositoryRegistry)


#: A scan with nothing in it, for the tests that are about admission rather than about
#: scanning. Passing a clean summary rather than omitting the arguments keeps these tests
#: on the same code path production uses; omitting them would take the opt-out branch and
#: quietly stop exercising the gate at all.
def clean_image_scan() -> ImageScanSummary:
    return ImageScanSummary(
        schema_version=1,
        status="COMPLETE",
        scanned_at=datetime(2026, 7, 26, 22, 5, 49, tzinfo=UTC),
    )


def load_image_scan_registry() -> ImageScanExceptionRegistry:
    return load_yaml(
        PROJECT_ROOT / "config" / "image-exceptions.yaml", ImageScanExceptionRegistry
    )


def workload_profile(name: str) -> WorkloadProfile:
    return next(workload for workload in load_workload_catalog().workloads if workload.name == name)


#: What the registry holds for ``COMMIT_SHA`` unless a test says otherwise: one image,
#: which is all one commit can have while the tag is twelve characters of it and both ECR
#: repositories are IMMUTABLE. The form below names no digest, because deriving one is what
#: a submission does now and pinning one is the exception.
def published_from_the_commit() -> tuple[PublishedImage, ...]:
    return (PublishedImage(image_digest=IMAGE_DIGEST, pushed_at=FIRST_PUSH),)


def cpu_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repository": "OLMo-core",
        "commit_sha": COMMIT_SHA,
        "workload_profile": CPU_WORKLOAD,
        "dataset_release": REGISTERED_DATASET,
        "team": "data-prep",
        "wandb_project": "olmo-core-tokenize",
        "command": ["python", "-m", "olmo_core.data.tokenize"],
    }
    payload.update(overrides)
    return payload


def olmo_payload(**overrides: object) -> dict[str, object]:
    return cpu_payload(
        **{
            "repository": "OLMo-core",
            "workload_profile": OLMO_WORKLOAD,
            "team": "modeling",
            "wandb_project": "olmo-core-extended",
            "command": ["python", "-m", "olmo_core.train"],
            **overrides,
        }
    )


def submission_inputs(payload: Mapping[str, object]) -> SubmissionInputs:
    return SubmissionInputs.model_validate(dict(payload))


def compile_payload(
    payload: Mapping[str, object],
    *,
    policy: ApprovalPolicy | None = None,
    image_scan_registry: ImageScanExceptionRegistry | None = None,
    image_scan_summary: ImageScanSummary | None = None,
    published_images: Sequence[PublishedImage] | None = None,
) -> CompiledSubmission:
    return compile_submission(
        submission_inputs(payload),
        run_id=RUN_ID,
        policy=policy if policy is not None else load_approval_policy(),
        repositories=load_repository_registry(),
        catalog=load_workload_catalog(),
        dataset_registry=load_dataset_registry(),
        image_scan_registry=(
            image_scan_registry
            if image_scan_registry is not None
            else ImageScanExceptionRegistry(schema_version=1)
        ),
        image_scan_summary=(
            image_scan_summary if image_scan_summary is not None else clean_image_scan()
        ),
        published_images=(
            published_images if published_images is not None else published_from_the_commit()
        ),
    )


def render(
    submission: CompiledSubmission,
    *,
    policy: ApprovalPolicy | None = None,
) -> str:
    return render_approver_context(
        submission,
        submitter=SUBMITTER,
        policy=policy if policy is not None else load_approval_policy(),
        repository_url=REPOSITORY_URL,
    )


def context_fragments(
    submission: CompiledSubmission,
    *,
    policy: ApprovalPolicy,
) -> dict[str, str]:
    manifest = submission.manifest
    cost = submission.cost
    return {
        "submitter": f"| Submitter | `{SUBMITTER}` |",
        "team": f"| Team claimed | `{manifest.team}` |",
        "repository": f"| Repository | [{manifest.repository}]({REPOSITORY_URL}) |",
        "linked commit": (
            f"| Commit | [`{manifest.commit_sha[:12]}`]"
            f"({REPOSITORY_URL}/commit/{manifest.commit_sha}) |"
        ),
        "image digest": f"| Image digest | `{manifest.image_digest}` |",
        "dataset release": f"| Dataset release | `{manifest.dataset_release}` |",
        "workload profile": f"| Workload profile | `{manifest.workload_profile}` |",
        "compute profile and its rate": (
            f"| Compute profile | `{manifest.compute_profile}` at ${cost.hourly_rate_usd}/hour |"
        ),
        "policy version": f"| Policy version | `{policy.policy_version}` |",
        "classification": f"**{submission.approval_class.value.upper()}**",
        "gate": f"`{submission.approving_environment.value}` gate",
        "cost arithmetic": (
            f"`${cost.hourly_rate_usd}/hour x {cost.nodes} node(s) x "
            f"{cost.maximum_runtime_hours}h x {cost.maximum_attempts} attempt(s) x "
            f"{cost.cells} cell(s)` = **${cost.maximum_compute_cost_usd}**"
        ),
        "manifest digest": submission.manifest_sha256,
    }


def exception_bullets(summary: str) -> list[str]:
    heading = "## Why this is an exception"
    assert heading in summary
    after_heading = summary.split(heading, maxsplit=1)[1]
    return [
        line.removeprefix("- ")
        for line in after_heading.splitlines()
        if line.startswith("- ")
    ]


@pytest.mark.parametrize("declared", PARTIAL_FANOUTS, ids=[",".join(f) for f in PARTIAL_FANOUTS])
def test_a_partially_declared_fanout_is_rejected(declared: tuple[str, ...]) -> None:
    overrides = {field: FANOUT_FIELDS[field] for field in declared}
    with pytest.raises(ValidationError) as exc_info:
        submission_inputs(cpu_payload(**overrides))
    assert any(
        "a fan-out must declare its size, its parallelism and what its index varies" in item["msg"]
        for item in exc_info.value.errors()
    ), f"expected the whole-or-absent message, got {exc_info.value.errors()}"


def test_a_fanout_declared_in_full_is_accepted() -> None:
    inputs = submission_inputs(cpu_payload(**FANOUT_FIELDS))

    assert inputs.fanout_size == 4
    assert inputs.fanout_parallelism == 2
    assert inputs.fanout_index_parameter == "seed"


def test_a_form_that_declares_no_fanout_is_accepted() -> None:
    inputs = submission_inputs(cpu_payload())

    assert inputs.fanout_size is None
    assert inputs.fanout_parallelism is None
    assert inputs.fanout_index_parameter is None


def test_the_form_fits_inside_the_workflow_dispatch_input_ceiling() -> None:
    declared = len(SubmissionInputs.model_fields)

    assert declared <= WORKFLOW_DISPATCH_INPUT_CEILING, (
        f"the form declares {declared} inputs; a workflow_dispatch trigger over "
        f"{WORKFLOW_DISPATCH_INPUT_CEILING} fails schema validation rather than degrading"
    )


def test_the_checkpoint_contract_is_not_something_a_submitter_can_contradict() -> None:
    assert "checkpoint" not in SubmissionInputs.model_fields


def test_the_form_rejects_a_property_it_does_not_define() -> None:
    with pytest.raises(ValidationError) as exc_info:
        submission_inputs(cpu_payload(approval_class="routine"))
    assert any(item["type"] == "extra_forbidden" for item in exc_info.value.errors())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", ""),
        ("commit_sha", "main"),
        ("commit_sha", "A" * 40),
        ("image_digest", "latest"),
        ("workload_profile", ""),
        ("dataset_release", ""),
        ("team", ""),
        ("wandb_project", ""),
        ("command", []),
        ("compute_profile", ""),
        ("maximum_runtime_hours", "0"),
        ("maximum_runtime_hours", 2),
        ("maximum_attempts", 0),
        ("fanout_size", 1),
    ],
)
def test_the_form_rejects_a_value_outside_the_range_it_declares(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        submission_inputs(cpu_payload(**{field: value}))
    assert exc_info.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    ("payload_factory", "workload_name"),
    [(cpu_payload, CPU_WORKLOAD), (olmo_payload, OLMO_WORKLOAD)],
    ids=[CPU_WORKLOAD, OLMO_WORKLOAD],
)
def test_the_workload_profile_supplies_what_the_form_did_not_ask_for(
    payload_factory: Callable[[], dict[str, object]],
    workload_name: str,
) -> None:
    compiled = compile_payload(payload_factory())
    workload = workload_profile(workload_name)
    manifest = compiled.manifest

    assert manifest.workload_profile == workload.name
    assert manifest.compute_profile == workload.compute_profile
    assert manifest.maximum_runtime_hours == workload.maximum_runtime_hours
    assert manifest.maximum_attempts == workload.maximum_attempts
    assert manifest.checkpoint == workload.checkpoint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compute_profile", "gpu-1xt4"),
        ("maximum_runtime_hours", Decimal(3)),
        ("maximum_attempts", 2),
    ],
)
def test_an_explicit_override_wins_over_the_profile_default(
    field: str,
    value: object,
) -> None:
    workload = workload_profile(OLMO_WORKLOAD)
    assert getattr(workload, field) != value, (
        "an override that matched the default would prove nothing about which one was used"
    )

    compiled = compile_payload(olmo_payload(**{field: value}))

    assert getattr(compiled.manifest, field) == value


def test_an_overridden_runtime_is_what_the_submission_is_priced_on() -> None:
    default = compile_payload(olmo_payload())
    longer = compile_payload(olmo_payload(maximum_runtime_hours="3"))

    assert default.cost.maximum_runtime_hours == workload_profile(OLMO_WORKLOAD).maximum_runtime_hours
    assert longer.cost.maximum_runtime_hours == Decimal(3)
    assert longer.cost.maximum_compute_cost_usd == Decimal("17.02")
    assert longer.facts.estimated_cost_usd == longer.cost.maximum_compute_cost_usd


def test_the_cost_is_recomputed_from_the_rate_the_catalog_records() -> None:
    compiled = compile_payload(cpu_payload())
    profile = next(
        candidate
        for candidate in load_workload_catalog().compute_profiles
        if candidate.name == compiled.manifest.compute_profile
    )

    assert compiled.cost.hourly_rate_usd == profile.hourly_rate_usd
    assert compiled.cost.nodes == profile.nodes
    # One hour on c7i.8xlarge at the rate the catalog records, quantized once. The number
    # is written out rather than recomputed, because a test that repeats the arithmetic
    # under test would agree with any arithmetic.
    assert compiled.cost.maximum_compute_cost_usd == Decimal("1.43")
    assert compiled.facts.estimated_cost_usd == compiled.cost.maximum_compute_cost_usd


def test_a_fanout_declared_on_the_form_reaches_the_manifest_and_the_price() -> None:
    compiled = compile_payload(
        cpu_payload(fanout_size=5, fanout_parallelism=5, fanout_index_parameter="seed")
    )

    assert compiled.manifest.fanout == FanOut(size=5, max_parallel=5, index_parameter="seed")
    assert compiled.cost.cells == 5
    assert compiled.facts.fanout_size == 5
    assert compiled.facts.fanout_parallelism == 5


def test_a_form_without_a_fanout_compiles_to_a_manifest_without_one() -> None:
    compiled = compile_payload(cpu_payload())

    assert compiled.manifest.fanout is None
    assert compiled.cost.cells == 1


def test_an_unregistered_workload_profile_is_refused_and_the_catalog_is_quoted() -> None:
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(cpu_payload(workload_profile="dolma-tokenize-enormous"))

    message = str(exc_info.value)
    assert "unregistered workload profile 'dolma-tokenize-enormous'" in message
    assert DOLMA_WORKLOAD in message
    assert OLMO_WORKLOAD in message


def test_an_unregistered_dataset_is_refused_before_a_reviewer_is_asked() -> None:
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(cpu_payload(dataset_release=UNREGISTERED_DATASET))
    assert "unregistered_dataset" in str(exc_info.value)


def test_an_unregistered_compute_profile_is_refused_because_it_cannot_be_priced() -> None:
    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(olmo_payload(compute_profile=UNREGISTERED_COMPUTE_PROFILE))

    message = str(exc_info.value)
    assert f"unregistered compute profile '{UNREGISTERED_COMPUTE_PROFILE}'" in message
    assert "no rate" in message


def test_a_refusal_that_policy_would_only_have_classified_still_happens_at_compile_time() -> None:
    lenient = load_approval_policy().model_copy(
        update={"denied_outright": ("mutable_image_reference",)}
    )
    classified = compile_payload(cpu_payload(dataset_release=UNREGISTERED_DATASET), policy=lenient)

    assert classified.approval_class is ApprovalClass.EXCEPTION
    assert classified.facts.dataset_registered is False

    with pytest.raises(SubmissionRefusedError):
        compile_payload(cpu_payload(dataset_release=UNREGISTERED_DATASET))


def test_compiling_is_given_nothing_that_would_let_it_ask_a_reviewer() -> None:
    parameters = set(inspect.signature(compile_submission).parameters)

    assert parameters.isdisjoint({"submitter", "approver", "approving_environment"}), (
        "the compile step runs without an id-token permission and before a gate, so it "
        "cannot be the thing that names or consults an approver"
    )


def test_the_same_inputs_compile_to_the_same_manifest_digest_twice() -> None:
    first = compile_payload(cpu_payload())
    second = compile_payload(cpu_payload())

    assert first.manifest == second.manifest
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.manifest_sha256 == sha256_digest(first.manifest)


def test_the_order_of_the_form_fields_does_not_change_the_manifest_digest() -> None:
    payload = cpu_payload()
    reordered = dict(reversed(list(payload.items())))
    assert list(reordered) != list(payload)

    assert compile_payload(reordered).manifest_sha256 == compile_payload(payload).manifest_sha256


@pytest.mark.parametrize(
    ("payload", "approval_class", "environment"),
    [
        (cpu_payload(), ApprovalClass.ROUTINE, ApprovalEnvironment.LEAD),
        (
            olmo_payload(maximum_runtime_hours="13"),
            ApprovalClass.EXCEPTION,
            ApprovalEnvironment.ADMIN,
        ),
    ],
    ids=["routine", "exception"],
)
def test_the_compiled_submission_names_the_gate_its_class_demands(
    payload: dict[str, object],
    approval_class: ApprovalClass,
    environment: ApprovalEnvironment,
) -> None:
    compiled = compile_payload(payload)

    assert compiled.approval_class is approval_class
    assert compiled.approving_environment is environment
    assert compiled.run_id == RUN_ID


def test_the_fragment_table_this_module_uses_covers_every_field_it_names() -> None:
    compiled = compile_payload(cpu_payload())

    assert set(context_fragments(compiled, policy=load_approval_policy())) == set(
        REQUIRED_CONTEXT_FIELDS
    )


@pytest.mark.parametrize("field", REQUIRED_CONTEXT_FIELDS)
def test_the_summary_states_every_field_the_reviewer_must_see(field: str) -> None:
    policy = load_approval_policy()
    compiled = compile_payload(cpu_payload())
    summary = render(compiled, policy=policy)

    assert context_fragments(compiled, policy=policy)[field] in summary


def test_the_cost_is_shown_as_a_product_rather_than_only_a_total() -> None:
    compiled = compile_payload(
        cpu_payload(fanout_size=4, fanout_parallelism=2, fanout_index_parameter="seed")
    )
    summary = render(compiled)
    cost = compiled.cost

    assert (
        f"`${cost.hourly_rate_usd}/hour x {cost.nodes} node(s) x "
        f"{cost.maximum_runtime_hours}h x {cost.maximum_attempts} attempt(s) x "
        f"{cost.cells} cell(s)` = **${cost.maximum_compute_cost_usd}**"
    ) in summary
    assert "x 4 cell(s)" in summary, (
        "a bare dollar figure invites a rubber stamp; the factors are what show which of "
        "them is the large one"
    )
    assert "This is the ceiling, not an estimate." in summary


def test_the_summary_states_the_hash_that_will_be_rechecked_inside_aws() -> None:
    compiled = compile_payload(cpu_payload())
    summary = render(compiled)

    assert f"Manifest SHA-256 `{compiled.manifest_sha256}`" in summary
    assert "Recomputed inside AWS" in summary


def test_a_routine_summary_carries_no_exception_section() -> None:
    summary = render(compile_payload(cpu_payload()))

    assert "## Why this is an exception" not in summary
    assert "**ROUTINE**" in summary


@pytest.mark.parametrize(("ceiling", "form", "phrase"), EXCEEDED_CEILINGS, ids=CEILING_IDS)
def test_an_exception_says_in_words_which_routine_ceiling_it_exceeded(
    ceiling: str,
    form: dict[str, object],
    phrase: str,
) -> None:
    compiled = compile_payload(olmo_payload(**form))
    summary = render(compiled)
    bullets = exception_bullets(summary)

    assert compiled.approval_class is ApprovalClass.EXCEPTION
    assert len(bullets) == 1, (
        f"{ceiling} was meant to be the only ceiling this submission exceeded; got {bullets}"
    )
    assert phrase in bullets[0]


def test_an_exception_over_two_ceilings_names_both_of_them() -> None:
    compiled = compile_payload(olmo_payload(maximum_runtime_hours="13", maximum_attempts=3))
    bullets = exception_bullets(render(compiled))

    assert len(bullets) == 2
    assert any("runtime bound of 13h" in bullet for bullet in bullets)
    assert any("attempt bound of 3" in bullet for bullet in bullets)


def test_an_exception_no_ceiling_explains_says_that_in_words_too() -> None:
    lenient = load_approval_policy().model_copy(
        update={"denied_outright": ("mutable_image_reference",)}
    )
    compiled = compile_payload(cpu_payload(dataset_release=UNREGISTERED_DATASET), policy=lenient)
    bullets = exception_bullets(render(compiled, policy=lenient))

    assert compiled.approval_class is ApprovalClass.EXCEPTION
    assert bullets == [
        (
            "No routine ceiling is exceeded; the submission is an exception because one of "
            "its inputs is not registered."
        )
    ]


@pytest.mark.parametrize(("ceiling", "form", "phrase"), EXCEEDED_CEILINGS, ids=CEILING_IDS)
def test_an_exception_summary_still_carries_everything_a_routine_one_does(
    ceiling: str,
    form: dict[str, object],
    phrase: str,
) -> None:
    policy = load_approval_policy()
    compiled = compile_payload(olmo_payload(**form))
    summary = render(compiled, policy=policy)
    fragments = context_fragments(compiled, policy=policy)

    assert phrase in summary
    assert "**EXCEPTION**" in summary
    for field in REQUIRED_CONTEXT_FIELDS:
        assert fragments[field] in summary, f"{ceiling} summary omitted {field}"


# ---------------------------------------------------------------------------------------
# The repository and the workload profile have to be the same repository
# ---------------------------------------------------------------------------------------


def test_a_workload_profile_from_another_repository_is_refused() -> None:
    """Mutation: drop the comparison and let the two fields disagree.

    MEASURED BEFORE IT WAS FIXED: a submission naming repository OLMo-core with workload
    profile dolma-tokenize-smoke compiled cleanly, classified routine, and routed to a
    lead. Two fields that must agree, and nothing compared them -- the same defect shape as
    the three-way output-prefix disagreement Phase 4 inherited.

    What would have run is whichever image the digest named, under a workload contract
    written for a different codebase: the runtime bound, the attempt bound and the
    checkpoint contract would all have been the other repository's.
    """
    with pytest.raises(SubmissionRefusedError, match="belongs to repository"):
        compile_payload(olmo_payload(workload_profile="dolma-tokenize-smoke"))


def test_the_refusal_names_both_repositories_so_the_reader_knows_which_to_change() -> None:
    """Mutation: refuse with a message naming only the workload.

    Either field could be the wrong one. A message saying only that the workload is wrong
    sends somebody to change the workload when they meant to change the repository, and
    the second attempt fails for the same reason with the same message.
    """
    with pytest.raises(SubmissionRefusedError) as refusal:
        compile_payload(olmo_payload(workload_profile="dolma-tokenize-smoke"))

    assert "dolma" in str(refusal.value)
    assert "OLMo-core" in str(refusal.value)


def test_a_workload_profile_from_the_declared_repository_is_accepted() -> None:
    """The other half. Mutation: compare the wrong way round, or always refuse.

    A check that refused everything would pass the two tests above and stop the platform
    working, which is why the agreeing case is asserted beside the disagreeing ones.
    """
    compiled = compile_payload(olmo_payload())

    assert compiled.manifest.repository == "OLMo-core"
    assert compiled.manifest.workload_profile == OLMO_WORKLOAD


# ---------------------------------------------------------------------------------------
# The image a run uses is derived from the commit it declares
# ---------------------------------------------------------------------------------------


def test_the_form_no_longer_asks_for_a_digest_copied_out_of_a_build_log() -> None:
    """Mutation: leave the field required.

    Seventy-one characters transcribed by hand out of another repository's build output was
    the hardest field on the form, and the only reason it was required is that nothing
    derived it. What survives is an override, so the shape it must have has not moved.
    """
    field = SubmissionInputs.model_fields["image_digest"]

    assert field.is_required() is False
    assert submission_inputs(cpu_payload()).image_digest is None
    with pytest.raises(ValidationError):
        submission_inputs(cpu_payload(image_digest="latest"))


def test_a_submission_that_names_no_digest_runs_the_image_its_commit_published() -> None:
    """Mutation: leave the manifest reading ``inputs.image_digest``.

    That is what it did, and it is how a lineage record could name commit A beside an image
    built from commit B: two required fields, both well formed, and nothing anywhere holding
    them up against each other.
    """
    compiled = compile_payload(cpu_payload())

    assert compiled.manifest.image_digest == IMAGE_DIGEST
    assert compiled.resolved_image.image_digest == IMAGE_DIGEST
    assert compiled.resolved_image.was_overridden is False
    assert compiled.resolved_image.chosen_from == 1


def test_a_commit_built_twice_runs_the_image_the_registry_took_most_recently() -> None:
    # A rebuild happens because the previous build was wrong, so an older image is a silent
    # revert of whatever the rebuild fixed. The pair is handed over in the order the
    # registry answered rather than in the order they were pushed.
    compiled = compile_payload(
        cpu_payload(),
        published_images=(
            PublishedImage(image_digest=REBUILT_DIGEST, pushed_at=SECOND_PUSH),
            PublishedImage(image_digest=IMAGE_DIGEST, pushed_at=FIRST_PUSH),
        ),
    )

    assert compiled.manifest.image_digest == REBUILT_DIGEST
    assert compiled.resolved_image.chosen_from == 2
    assert compiled.resolved_image.was_overridden is False


def test_a_digest_the_declared_commit_published_is_honoured_and_recorded_as_a_pin() -> None:
    """The rebuild-and-pin path, which is the reason the field survives at all.

    A researcher reproducing an earlier result needs the image that produced it rather than
    the newest one, and ``was_overridden`` is what lets a reader of the record tell the two
    apart -- without it a pinned older build and a derived newest one read identically.
    """
    compiled = compile_payload(
        cpu_payload(image_digest=IMAGE_DIGEST),
        published_images=(
            PublishedImage(image_digest=IMAGE_DIGEST, pushed_at=FIRST_PUSH),
            PublishedImage(image_digest=REBUILT_DIGEST, pushed_at=SECOND_PUSH),
        ),
    )

    assert compiled.manifest.image_digest == IMAGE_DIGEST
    assert compiled.resolved_image.was_overridden is True
    assert compiled.resolved_image.chosen_from == 2


def test_a_digest_from_another_commit_is_refused_before_a_reviewer_is_asked() -> None:
    # The defect the derivation closes for anybody who leaves the field alone, arriving
    # through the field that survives. Refused here rather than at admission because
    # nothing about it needs the account.
    with pytest.raises(SubmissionRefusedError) as refusal:
        compile_payload(cpu_payload(image_digest=DIGEST_FROM_ANOTHER_COMMIT))

    message = str(refusal.value)
    assert DIGEST_FROM_ANOTHER_COMMIT in message
    assert COMMIT_SHA in message


def test_a_commit_nothing_has_been_built_from_is_refused_and_told_to_build_it() -> None:
    """Mutation: compile it anyway and let admission refuse it.

    It does reach admission today, and comes back refused for unreviewed image-scan
    findings -- true, and pointing at the wrong thing, because there is no scan of an image
    nobody built. A refusal naming the wrong next step costs more than none, since it gets
    followed.
    """
    with pytest.raises(SubmissionRefusedError) as refusal:
        compile_payload(cpu_payload(), published_images=())

    assert "build-research-image.yml" in str(refusal.value)


def test_the_manifest_names_an_image_even_though_the_form_no_longer_has_to() -> None:
    """Mutation: make ``RunManifest.image_digest`` optional to match the form.

    The two fields answer different questions. A form may leave the image to be derived; a
    lineage record must never be able to say that the image a run used is unknown, and the
    field being required is the whole of how it cannot. Relaxing it would also move the
    canonical hash, the schema version and the structural digests recorded in four
    committed proof bundles, in order to express a state the record exists to exclude.
    """
    field = RunManifest.model_fields["image_digest"]
    compiled = compile_payload(cpu_payload())

    assert field.is_required() is True
    assert compiled.manifest.image_digest == compiled.resolved_image.image_digest
    with pytest.raises(ValidationError):
        RunManifest.model_validate(
            {
                key: value
                for key, value in compiled.manifest.model_dump(mode="json").items()
                if key != "image_digest"
            }
        )


def test_the_derived_image_is_what_the_reviewer_is_shown() -> None:
    # The approver reads a digest nobody typed in, so the summary has to carry the resolved
    # one rather than the field it replaced -- which is empty on almost every submission.
    compiled = compile_payload(cpu_payload())

    assert f"| Image digest | `{IMAGE_DIGEST}` |" in render(compiled)
