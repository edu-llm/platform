"""What the researcher role is built from, decided without touching AWS.

The role itself is a CloudFormation template and the janitor is a Lambda. This module is the
half of both that is a pure function of committed configuration: which instance types the role
permits, and the two clocks the janitor runs on. Keeping it here is what lets
tests/test_researcher_role_template.py compare the template's condition against the catalog
without rendering anything, and what lets tests/test_expiry_janitor.py decide a sweep with no
account in the loop.

docs-frank/reference/aws-spend-controls.md is the specification for the role. Read "The
permission policy" before changing anything here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.reviewed_configuration import ConfigFile, load_config_file

__all__ = [
    "EXPIRES_AT_TAG_KEY",
    "GOVERNANCE_TAG_KEYS",
    "PROJECT_TAG_KEY",
    "RESEARCHER_ROLE_NAME",
    "WARNING_TAG_KEY",
    "LaneSettings",
    "instance_types_the_catalog_prices",
    "load_lane_settings",
]

#: WITHOUT THE sbsandbox-intern-edullm- PREFIX EVERY OTHER ROLE HERE CARRIES, deliberately.
#: system-overview.md and aws-spend-controls.md both name it edullm-researcher, and a person
#: types this name into an assume-role call rather than reading it off a template. Two
#: consequences worth knowing before renaming it: the deployer role's resource scopes key on
#: the long prefix and therefore do not reach this role, which is correct because no pipeline
#: may touch it; and InternSandboxBoundary's DenyTamperingWithInternRoles matches role/Intern-*
#: and therefore does not either.
RESEARCHER_ROLE_NAME: Final = "edullm-researcher"

#: The two tags every launch through the lane must carry, spelled exactly as the IAM condition
#: keys spell them. Capitalised because aws:RequestTag is case-sensitive and the helper, the
#: policy and the janitor all have to agree; a lowercase project tag satisfies nothing.
PROJECT_TAG_KEY: Final = "Project"
EXPIRES_AT_TAG_KEY: Final = "ExpiresAt"

#: The tag the janitor writes when it warns, and reads to decide whether it may stop.
#:
#: A tag on the instance rather than a record somewhere else, for two reasons. It is colocated
#: with the thing it is about, so anybody looking at a machine can see it was warned; and it
#: survives the janitor being redeployed, which a value in memory would not. The prefixed
#: spelling keeps it out of GOVERNANCE_TAG_KEYS below, so the role's tag-stripping deny does
#: not accidentally cover a key the janitor has to write.
WARNING_TAG_KEY: Final = "edullm:expiry-warned-at"

#: The tags a launched instance may not have removed from it afterwards. From
#: aws-spend-controls.md, "The permission policy", statement
#: DenyStrippingGovernanceTagsAfterLaunch. DO-NOT-TERMINATE is on the list and is not read by
#: anything this plan builds -- it is a tag the account's other tooling honours, and stripping
#: it is a governance act whether or not this platform acts on it.
GOVERNANCE_TAG_KEYS: Final[tuple[str, ...]] = (
    PROJECT_TAG_KEY,
    EXPIRES_AT_TAG_KEY,
    "DO-NOT-TERMINATE",
)


class LaneSettings(ContractModel):
    schema_version: Literal[1]
    default_lifetime_hours: int = Field(gt=0)
    warning_lead_minutes: int = Field(gt=0)
    sweep_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_clocks(self) -> Self:
        if self.warning_lead_minutes <= self.sweep_minutes:
            raise ValueError(
                "warning lead must exceed the sweep interval, or a machine can expire "
                "between two sweeps having never been warned and the sweep that finds it "
                "would both warn and stop it"
            )
        return self


def load_lane_settings(directory: Path | None = None) -> LaneSettings:
    """The three clocks, out of a resolved directory rather than out of a written-down path.

    This used to default to ``"config/reports/researcher-lane.yaml"``, a path resolved
    against whatever directory the process was started in. Every caller but one is
    ``edullm run`` and ``edullm shell``, which are used from a research repository and never
    from a platform checkout, so the default resolved for the suite and for nobody else.
    ``edullm_platform.reviewed_configuration`` carries the whole account of that.
    """
    return load_config_file(ConfigFile.RESEARCHER_LANE, LaneSettings, directory=directory)


def instance_types_the_catalog_prices(catalog: WorkloadCatalog) -> tuple[str, ...]:
    """Every instance type the compute catalog names, sorted, once each.

    THE CATALOG AND NOT THE PROVISIONED SUBSET. system-overview.md draws the allow-list as the
    whole compute catalog, and the seventeenth profile -- priced, unprovisioned, no Batch queue
    -- is a shape somebody may legitimately start outside Batch. Filtering on ``provisioned``
    would make the role narrower than the document says it is, and would silently widen and
    narrow itself as profiles are promoted.

    Sorted and deduplicated so the tuple is comparable by equality to the list in
    infra/iam/researcher-role.yaml. Two profiles sharing an instance type is not hypothetical:
    nothing in WorkloadCatalog forbids it, and only the profile names are held unique.
    """
    return tuple(sorted({profile.instance_type for profile in catalog.compute_profiles}))
