"""What two builds of one commit from one registered base agree and disagree about.

Phase 1 criterion 2 asks that rebuilding identical inputs be *explainable*, and is
careful not to ask that it be reproducible. The distinction is the whole of this module:
an image built twice from the same commit and the same digest-pinned base does not get
the same digest, and the useful claim is not that it should — it is that every field
which differs has a named cause, and that no field derived from a pinned input is among
them.

**Why the workflow cannot produce this comparison.** The publish workflow looks the tag
up before it builds, and a re-run of the same commit resolves to the digest already in
the registry and skips the build. That is correct behaviour: ECR tags are immutable, the
run-URL label guarantees a second build would have a different manifest digest, and the
tag cannot be rewritten, so a re-run that rebuilt would fail permanently instead of
resuming. The consequence is that the shipped path can never build one commit twice, and
the comparison has to be produced deliberately, outside it. ``tools/record_local_rebuilds.py``
is how; this module is what reads the result.

**What is compared.** The image configuration blob, flattened to one leaf per line so
that a difference names a field rather than reporting that two documents differ. The
configuration is the right object rather than the manifest: the manifest is a list of
digests over the configuration and the layers, so it differs whenever any of them does
and says nothing about which.

**The three causes, and why they are a closed list.** Every difference observed between
two local builds of one commit falls into one of :data:`NONDETERMINISM_CAUSES`, and a
comparison that produced a difference outside it would be reporting something nobody has
explained — which is exactly the state criterion 2 says must not go unnoticed. So an
unexplained difference is a failure here rather than a line in a report.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, model_validator

from edullm_platform.contracts.base import (
    ContractModel,
    Sha256Digest,
    require_ordered_sequence,
)
from edullm_platform.contracts.manifest import COMMIT_SHA_PATTERN
from edullm_platform.evidence import redact_content_digests, scan_for_secrets

__all__ = [
    "NONDETERMINISM_CAUSES",
    "PINNED_INPUT_FIELDS",
    "ConfigurationField",
    "FieldDifference",
    "LocalRebuildComparison",
    "NondeterminismCause",
    "RebuiltImage",
    "UnexplainedDifferenceError",
    "cause_for",
    "compare_builds",
    "unexplained",
]


#: A sha256 written without its algorithm prefix. ``redact_content_digests`` masks
#: ``sha256:…`` and a 40-character commit SHA, and neither covers this: the base image's
#: own environment carries ``PYTHON_SHA256=<64 hex characters>``, which the generic
#: sixty-character credential pattern refuses on sight.
#:
#: Masking it is a narrow exemption and worth stating exactly. A 64-character run of
#: lowercase hexadecimal is not any AWS credential shape the scanner knows — an access
#: key id is twenty characters beginning ``AKIA`` or ``ASIA``, a secret access key is
#: forty characters of base64, a session token begins with a fixed prefix — so nothing
#: the scanner could have caught is lost. What is given up is the generic net's reach
#: over a 64-character hexadecimal secret of some other kind, in this one field type.
BARE_SHA256_HEX: Final = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{64}(?![0-9a-fA-F])")


#: An http or https URL that cannot be carrying a credential, matched conservatively.
#: The path alphabet deliberately excludes ``@``, ``?``, ``#``, ``%``, ``+``, ``=`` and
#: ``&``, which are where a credential in a URL lives — userinfo, a query parameter, a
#: percent-encoded token. A URL that has any of them is matched only as far as the
#: character before, so its credential-bearing tail is still scanned. Masking less than
#: the whole URL is the safe direction to be wrong in.
#:
#: This exists because of a real value in a real image. The publish workflow's per-run
#: label is a GitHub Actions run URL, and the tail of one —
#: ``core/actions/runs/<id>/attempts/1`` — is forty characters drawn from exactly the
#: alphabet an AWS secret access key uses, so the generic pattern refuses it.
CREDENTIAL_FREE_URL: Final = re.compile(
    r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._~/-]*)?"
)


def scan_configuration_value(value: str) -> str:
    """Scan a configuration value, having first masked the shapes it is made of.

    Three token shapes in an image configuration match the generic long-credential
    patterns and are none of them credentials: a content digest, a bare sha256 written
    without its prefix, and a credential-free URL. Each is masked by an exact pattern and
    everything else is scanned unchanged. Digests are masked before URLs, because a URL
    path can carry one and masking the URL first would hide it from the digest mask.
    """
    masked = BARE_SHA256_HEX.sub("<sha256-hex>", redact_content_digests(value))
    scan_for_secrets(CREDENTIAL_FREE_URL.sub("<url>", masked))
    return value


ConfigurationValue = Annotated[str, AfterValidator(scan_configuration_value)]


class ConfigurationField(ContractModel):
    """One leaf of an image configuration, by dotted path and value.

    The value is the JSON encoding of the leaf rather than the leaf itself, so a string
    and the number that prints the same way cannot compare equal, and so a record of one
    build is comparable to another without either side knowing the schema.
    """

    path: str = Field(min_length=1, max_length=256)
    value: ConfigurationValue = Field(max_length=8192)


class RebuiltImage(ContractModel):
    """One build, its configuration digest, and the configuration flattened out.

    ``build`` is a label rather than a digest because two builds of the same inputs are
    told apart by the order they were run in, not by anything intrinsic — that being the
    point. ``description`` says what was varied, if anything, and is required: a build
    recorded without one is a column in a comparison nobody can interpret.
    """

    build: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9-]{0,31}$")
    description: str = Field(min_length=1, max_length=512)
    config_digest: Sha256Digest
    fields: Annotated[tuple[ConfigurationField, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )

    @model_validator(mode="after")
    def validate_paths_are_unique(self) -> Self:
        paths = [field.path for field in self.fields]
        if len(set(paths)) != len(paths):
            raise ValueError("a configuration field path may appear once")
        return self

    def field_map(self) -> dict[str, str]:
        return {field.path: field.value for field in self.fields}


class LocalRebuildComparison(ContractModel):
    """Several builds of one commit from one registered base, recorded side by side.

    The pinned inputs are recorded once, at the top, because they are what every build
    shares and what the comparison is a claim about. ``builder`` and ``platform`` are
    recorded because the answer depends on both: a different BuildKit writes different
    layer metadata, and this comparison says nothing about a builder it was not run on.
    """

    schema_version: Literal[1]
    source_commit_sha: str = Field(pattern=COMMIT_SHA_PATTERN)
    base_image_digest: Sha256Digest
    dockerfile_path: str = Field(min_length=1, max_length=256)
    build_context: str = Field(min_length=1, max_length=256)
    platform: str = Field(min_length=1, max_length=64)
    builder: str = Field(min_length=1, max_length=128)
    performed_at: datetime = Field(strict=False)
    builds: Annotated[tuple[RebuiltImage, ...], BeforeValidator(require_ordered_sequence)] = Field(
        min_length=2, strict=False
    )

    @model_validator(mode="after")
    def validate_build_labels_are_unique(self) -> Self:
        labels = [build.build for build in self.builds]
        if len(set(labels)) != len(labels):
            raise ValueError("a build label may appear once")
        return self

    def build_named(self, label: str) -> RebuiltImage:
        matching = [build for build in self.builds if build.build == label]
        if len(matching) != 1:
            raise KeyError(label)
        return matching[0]


@dataclass(frozen=True)
class FieldDifference:
    """One configuration field two builds disagree about."""

    path: str
    left: str
    right: str


@dataclass(frozen=True)
class NondeterminismCause:
    """One reason a field can differ between two builds of identical inputs.

    ``deliberate`` separates the two kinds. A per-run label differs because the build was
    told to make it differ, and removing it would cost the provenance it carries. A
    timestamp differs because BuildKit reads a clock, which nobody asked for and which
    ``SOURCE_DATE_EPOCH`` could pin if the phase ever needed byte-level reproducibility.
    """

    name: str
    pattern: re.Pattern[str]
    deliberate: bool
    detail: str


#: Every reason a field of the image configuration is allowed to differ between two
#: builds of one commit from one pinned base. Ordered most specific first, because the
#: label pattern and the generic field patterns do not overlap but a future entry might.
NONDETERMINISM_CAUSES: Final[tuple[NondeterminismCause, ...]] = (
    NondeterminismCause(
        name="per-run label",
        pattern=re.compile(r"^config\.Labels\.edullm\.workflow\.run\.url$"),
        deliberate=True,
        detail=(
            "The publish workflow labels every image with the URL of the run that built "
            "it, which is different on every run by construction. This is the one "
            "difference that is deliberate: it is what lets somebody holding a digest "
            "find the run that produced it, and it is also why a re-run of the same "
            "commit could never produce the same manifest digest even if everything else "
            "were pinned."
        ),
    ),
    NondeterminismCause(
        name="image creation timestamp",
        pattern=re.compile(r"^created$"),
        deliberate=False,
        detail=(
            "BuildKit stamps the configuration with the wall-clock instant the build "
            "finished. Nothing derives it from an input, so two builds a second apart "
            "differ here and two builds a month apart differ here by a month."
        ),
    ),
    NondeterminismCause(
        name="history entry timestamp",
        pattern=re.compile(r"^history\[\d+\]\.created$"),
        deliberate=False,
        detail=(
            "The same clock reading, recorded again against each step this build "
            "executed. The history entries inherited from the base image carry the base "
            "build's timestamps and are identical, because the base is pinned by digest; "
            "only the entries this Dockerfile adds move."
        ),
    ),
    NondeterminismCause(
        name="layer content timestamp",
        pattern=re.compile(r"^rootfs\.diff_ids\[\d+\]$"),
        deliberate=False,
        detail=(
            "A layer digest covers the tar of the layer, and a tar carries a modification "
            "time per entry. Two of the layers here are the build's own and each picks up "
            "a clock: the directory the WORKDIR creates is stamped with the instant the "
            "build ran, and the layer the source is copied into carries the modification "
            "times of the checkout it was copied from, which a fresh clone sets to the "
            "moment it ran. The bytes of every file are identical either way; the metadata "
            "around them is not. Layers inherited from the pinned base never move, because "
            "their tars are the base's and are fetched rather than built."
        ),
    ),
)

#: The fields that must be identical between two builds of one commit from one pinned
#: base, because each is derived from an input that was pinned. Stated as a list to be
#: checked rather than left implicit in the absence of a difference: a comparison whose
#: identical set quietly shrank would otherwise still pass.
PINNED_INPUT_FIELDS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"^architecture$"),
    re.compile(r"^os$"),
    re.compile(r"^config\.Cmd\[\d+\]$"),
    re.compile(r"^config\.Env\[\d+\]$"),
    re.compile(r"^config\.WorkingDir$"),
    re.compile(r"^config\.Labels\.org\.opencontainers\.image\.base\.name$"),
    re.compile(r"^config\.Labels\.org\.opencontainers\.image\.revision$"),
    re.compile(r"^config\.Labels\.org\.opencontainers\.image\.source$"),
    re.compile(r"^history\[\d+\]\.created_by$"),
    re.compile(r"^rootfs\.type$"),
)


class UnexplainedDifferenceError(ValueError):
    """Two builds differ in a field no recorded cause accounts for."""


def compare_builds(left: RebuiltImage, right: RebuiltImage) -> tuple[FieldDifference, ...]:
    """Every field the two configurations disagree about, in path order.

    A field present in one and absent from the other is a difference too, and is reported
    with ``<absent>`` on the side that lacks it rather than skipped: a build that dropped
    a label entirely would otherwise compare equal on every field it still had.
    """
    left_fields = left.field_map()
    right_fields = right.field_map()
    return tuple(
        FieldDifference(
            path=path,
            left=left_fields.get(path, "<absent>"),
            right=right_fields.get(path, "<absent>"),
        )
        for path in sorted(set(left_fields) | set(right_fields))
        if left_fields.get(path, "<absent>") != right_fields.get(path, "<absent>")
    )


def cause_for(path: str) -> NondeterminismCause | None:
    return next((cause for cause in NONDETERMINISM_CAUSES if cause.pattern.fullmatch(path)), None)


def unexplained(differences: Sequence[FieldDifference]) -> tuple[str, ...]:
    """The paths among these differences that no recorded cause accounts for."""
    return tuple(
        difference.path for difference in differences if cause_for(difference.path) is None
    )
