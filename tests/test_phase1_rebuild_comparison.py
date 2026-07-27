"""Two builds of one commit from one pinned base, and what they agree about.

Phase 1 criterion 2 asks that rebuilding identical inputs be *explainable*, not that it
be reproducible. This module is where that claim is checked, against a comparison of real
builds committed under ``fixtures/evidence/phase-1/rebuild/``.

The comparison could not be produced by the workflow and was not meant to be: the publish
job looks the tag up first, and a re-run of the same commit resumes to the digest already
in the registry rather than building again. So the builds were made deliberately, on a
laptop, with ``tools/record_local_rebuilds.py`` writing down what came out.

Five configurations are recorded and four comparisons are made against the first, each
isolating one thing:

``a`` vs ``b``
    Nothing varied. Two builds, no cache, the same source tree and the same labels.
``a`` vs ``c``
    Only the per-run label varied, which is what a genuine second run of the workflow
    would vary and could not avoid varying.
``a`` vs ``d``
    Only the file modification times of the source varied, which is what a checkout on a
    different machine varies without anybody choosing to.
``a`` vs ``published``
    Against the image the workflow actually published, whose configuration was fetched
    from the registry.

The assertions are of two kinds and both are needed. That every difference has a recorded
cause is the criterion. That every field derived from a pinned input is *not* a
difference is what stops the first from being satisfied by a list of causes long enough
to cover anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.build_tooling import load_registry
from edullm_platform.evidence import redact_content_digests, scan_for_secrets
from edullm_platform.rebuild_comparison import (
    NONDETERMINISM_CAUSES,
    PINNED_INPUT_FIELDS,
    ConfigurationField,
    LocalRebuildComparison,
    RebuiltImage,
    cause_for,
    compare_builds,
    unexplained,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPARISON_PATH = (
    PROJECT_ROOT / "fixtures" / "evidence" / "phase-1" / "rebuild" / "local-rebuild-comparison.json"
)
REGISTERED = load_registry(PROJECT_ROOT / "config" / "repositories.yaml").repository_by_name(
    "OLMo-core"
)

#: What each comparison against build ``a`` is expected to differ in, exactly. Written
#: out rather than derived, because "the differences are whatever they are" is not a
#: claim; this is the claim, and a build that started differing somewhere else has to
#: fail here rather than be absorbed.
EXPECTED_DIFFERENCES: dict[str, tuple[str, ...]] = {
    "b": ("created", "history[12].created"),
    "c": ("config.Labels.edullm.workflow.run.url", "created", "history[12].created"),
    "d": ("created", "history[12].created", "rootfs.diff_ids[5]"),
    "published": (
        "created",
        "history[10].created",
        "history[11].created",
        "history[12].created",
        "rootfs.diff_ids[4]",
        "rootfs.diff_ids[5]",
    ),
}


@pytest.fixture(scope="module")
def comparison() -> LocalRebuildComparison:
    return LocalRebuildComparison.model_validate_json(COMPARISON_PATH.read_text(encoding="utf-8"))


def paths_of(build: RebuiltImage) -> set[str]:
    return set(build.field_map())


# --------------------------------------------------------------------------------------
# What the recorded builds were builds of
# --------------------------------------------------------------------------------------


def test_the_builds_were_made_from_the_base_this_repository_registers(
    comparison: LocalRebuildComparison,
) -> None:
    # The comparison is a claim about building *the registered inputs* twice. If the
    # registered base moves, this record is about a build nobody would make now, and it
    # has to go red rather than keep reading as an explanation of the current one.
    assert comparison.base_image_digest == REGISTERED.base_image_digest
    assert comparison.dockerfile_path == REGISTERED.dockerfile_path
    assert comparison.build_context == REGISTERED.build_context


def test_the_recorded_builds_all_describe_the_same_shape_of_configuration(
    comparison: LocalRebuildComparison,
) -> None:
    # A field present in one build and missing from another would make every comparison
    # below report a difference that is really a difference in what was recorded.
    shapes = {build.build: paths_of(build) for build in comparison.builds}
    reference = shapes["a"]

    assert all(shape == reference for shape in shapes.values()), {
        label: sorted(shape ^ reference) for label, shape in shapes.items() if shape != reference
    }


def test_the_published_configuration_is_recorded_under_the_digest_the_registry_holds(
    comparison: LocalRebuildComparison,
) -> None:
    # The tool digests the bytes it was given rather than trusting a reported digest, so
    # this ties the recorded configuration to the one ECR serves for the published image.
    published = comparison.build_named("published")

    assert published.config_digest == (
        "sha256:8ae7bb5ff10ed0f2576d3df24e8890a255eba7602aee831184e7f3336b217b15"
    )


def test_no_two_builds_share_a_configuration_digest(comparison: LocalRebuildComparison) -> None:
    # The point of the exercise, stated as an assertion. Identical inputs do not produce
    # an identical image identity, and this is the fact criterion 2 exists to explain.
    digests = [build.config_digest for build in comparison.builds]

    assert len(set(digests)) == len(digests)


# --------------------------------------------------------------------------------------
# Where the nondeterminism is, and where it is not
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("other", sorted(EXPECTED_DIFFERENCES), ids=sorted(EXPECTED_DIFFERENCES))
def test_every_difference_from_the_first_build_has_a_recorded_cause(
    comparison: LocalRebuildComparison,
    other: str,
) -> None:
    differences = compare_builds(comparison.build_named("a"), comparison.build_named(other))

    assert unexplained(differences) == ()


@pytest.mark.parametrize("other", sorted(EXPECTED_DIFFERENCES), ids=sorted(EXPECTED_DIFFERENCES))
def test_the_differences_are_exactly_the_ones_recorded(
    comparison: LocalRebuildComparison,
    other: str,
) -> None:
    differences = compare_builds(comparison.build_named("a"), comparison.build_named(other))

    assert tuple(difference.path for difference in differences) == EXPECTED_DIFFERENCES[other]


def test_two_builds_of_identical_inputs_differ_only_in_two_clock_readings(
    comparison: LocalRebuildComparison,
) -> None:
    # The heart of it. Nothing was varied between a and b, and two fields moved: the
    # instant the image records for itself and the same instant against the one step this
    # Dockerfile executes. Neither is derived from anything that was pinned.
    differences = compare_builds(comparison.build_named("a"), comparison.build_named("b"))
    causes = {cause_for(difference.path) for difference in differences}

    assert len(differences) == 2
    assert all(cause is not None and not cause.deliberate for cause in causes)


def test_the_filesystem_the_image_carries_is_identical_when_nothing_varies(
    comparison: LocalRebuildComparison,
) -> None:
    # Two builds with the same source tree produce the same layers, byte for byte. That
    # is the half of reproducibility this build path does have, and it is the half that
    # matters for asking whether two images contain the same thing.
    left = comparison.build_named("a").field_map()
    right = comparison.build_named("b").field_map()
    layers = sorted(path for path in left if path.startswith("rootfs.diff_ids["))

    assert layers, "a configuration with no layers would pass this vacuously"
    assert [left[path] for path in layers] == [right[path] for path in layers]


def test_the_layers_inherited_from_the_pinned_base_never_move(
    comparison: LocalRebuildComparison,
) -> None:
    # Four of the six layers come from the base image, which is pinned by digest and
    # fetched rather than built. Those four are identical in every recorded build,
    # including the one the workflow published on a different machine.
    inherited = [f"rootfs.diff_ids[{index}]" for index in range(4)]
    values = {tuple(build.field_map()[path] for path in inherited) for build in comparison.builds}

    assert len(values) == 1


@pytest.mark.parametrize("other", sorted(EXPECTED_DIFFERENCES), ids=sorted(EXPECTED_DIFFERENCES))
def test_no_field_derived_from_a_pinned_input_ever_differs(
    comparison: LocalRebuildComparison,
    other: str,
) -> None:
    # Without this, the criterion could be satisfied by widening the list of causes until
    # it covered everything. The environment, the command, the working directory, the
    # architecture, the three content labels and every recorded build step are derived
    # from the commit and the registered base, and none of them is allowed to move.
    differences = compare_builds(comparison.build_named("a"), comparison.build_named(other))
    pinned = [
        difference.path
        for difference in differences
        if any(pattern.fullmatch(difference.path) for pattern in PINNED_INPUT_FIELDS)
    ]

    assert pinned == []


def test_every_pinned_field_pattern_matches_something_that_was_recorded(
    comparison: LocalRebuildComparison,
) -> None:
    # A pattern that matches nothing asserts nothing, and the test above would pass on an
    # empty list forever.
    recorded = paths_of(comparison.build_named("a"))
    unmatched = [
        pattern.pattern
        for pattern in PINNED_INPUT_FIELDS
        if not any(pattern.fullmatch(path) for path in recorded)
    ]

    assert unmatched == []


def test_only_one_of_the_recorded_causes_is_something_anybody_chose() -> None:
    # Worth separating, because the two kinds have different answers. The per-run label
    # differs because the build was told to make it differ and removing it would cost the
    # provenance it carries. The other three are clocks, which SOURCE_DATE_EPOCH could
    # pin if this phase ever needed byte-level reproducibility, and does not.
    deliberate = [cause.name for cause in NONDETERMINISM_CAUSES if cause.deliberate]

    assert deliberate == ["per-run label"]


# --------------------------------------------------------------------------------------
# What the record refuses
# --------------------------------------------------------------------------------------


def test_the_committed_comparison_carries_no_credential() -> None:
    text = COMPARISON_PATH.read_text(encoding="utf-8")
    masked = redact_content_digests(text)

    assert "AKIA" not in text
    assert "ASIA" not in text
    # The whole file is not scannable as one string: it is a list of image configurations
    # and holds bare sha256 hashes and URLs, which is what the per-field masking exists
    # for. What is asserted here is the part no exemption covers.
    assert "-----BEGIN" not in masked


def test_a_configuration_value_carrying_a_real_credential_is_still_refused() -> None:
    # The masking is three exact token shapes and nothing else. A sixty-character token
    # that is not a digest and not a URL is refused as it always was.
    with pytest.raises(ValidationError):
        ConfigurationField(path="config.Env[0]", value=json.dumps("TOKEN=" + "Zx1" * 25))


def test_a_url_with_a_query_string_is_not_masked_past_the_question_mark() -> None:
    # A credential in a URL lives in the userinfo or the query, so the URL mask stops at
    # the first character either could start with. What is left is scanned.
    with pytest.raises(ValidationError):
        ConfigurationField(
            path="config.Labels.example",
            value=json.dumps("https://example.com/x?token=" + "Zx1" * 25),
        )


def test_two_builds_cannot_share_a_label(comparison: LocalRebuildComparison) -> None:
    payload = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))
    payload["builds"].append(payload["builds"][0])

    with pytest.raises(ValidationError, match="build label"):
        LocalRebuildComparison.model_validate(payload)


def test_a_comparison_of_one_build_is_not_a_comparison() -> None:
    payload = json.loads(COMPARISON_PATH.read_text(encoding="utf-8"))
    payload["builds"] = payload["builds"][:1]

    with pytest.raises(ValidationError):
        LocalRebuildComparison.model_validate(payload)


def test_a_field_recorded_twice_in_one_build_is_refused() -> None:
    field = ConfigurationField(path="os", value=json.dumps("linux"))

    with pytest.raises(ValidationError, match="path may appear once"):
        RebuiltImage(
            build="a",
            description="x",
            config_digest="sha256:" + "1a" * 32,
            fields=(field, field),
        )


def test_a_field_present_in_one_build_and_absent_from_the_other_is_a_difference() -> None:
    # A build that dropped a label entirely would otherwise compare equal on every field
    # it still had, which is the one way a comparison can be wrong and look right.
    left = RebuiltImage(
        build="a",
        description="x",
        config_digest="sha256:" + "1a" * 32,
        fields=(
            ConfigurationField(path="os", value=json.dumps("linux")),
            ConfigurationField(path="config.Labels.example", value=json.dumps("kept")),
        ),
    )
    right = RebuiltImage(
        build="b",
        description="x",
        config_digest="sha256:" + "2b" * 32,
        fields=(ConfigurationField(path="os", value=json.dumps("linux")),),
    )

    differences = compare_builds(left, right)

    assert [difference.path for difference in differences] == ["config.Labels.example"]
    assert differences[0].right == "<absent>"


def test_a_difference_no_cause_explains_is_reported_as_unexplained() -> None:
    left = RebuiltImage(
        build="a",
        description="x",
        config_digest="sha256:" + "1a" * 32,
        fields=(ConfigurationField(path="config.User", value=json.dumps("root")),),
    )
    right = RebuiltImage(
        build="b",
        description="x",
        config_digest="sha256:" + "2b" * 32,
        fields=(ConfigurationField(path="config.User", value=json.dumps("nobody")),),
    )

    assert unexplained(compare_builds(left, right)) == ("config.User",)


def test_the_scan_still_refuses_an_account_id_in_a_configuration_value() -> None:
    with pytest.raises(ValidationError):
        ConfigurationField(path="config.Env[0]", value=json.dumps("REGISTRY=123456789012"))
    # And the helper it delegates to is the repository's, not a copy.
    assert scan_for_secrets("safe") == "safe"
