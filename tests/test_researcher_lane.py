"""The allow-list the researcher role is built from, and the settings the janitor reads.

The allow-list is the property most likely to be typed by hand and most expensive when it is.
docs-frank/reference/aws-spend-controls.md, under "Why an allow-list rather than a deny-list",
measures both failure directions on the same forty-one instance types: the family form breaks
four provisioned profiles and misses families AWS ships next. So it is read off the catalog,
and this module is the test that it is still being read off the catalog rather than copied.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.researcher_lane import (
    LaneSettings,
    instance_types_the_catalog_prices,
    load_lane_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def shipped_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def test_the_allow_list_names_a_type_the_catalog_prices_but_does_not_provision() -> None:
    """Mutation: filter on profile.provisioned.

    gpu-1xa10g-sagemaker is priced and not provisioned, and the overview draws the allow-list
    as the whole compute catalog rather than the submittable subset. Filtering on provisioned
    would refuse a shape a researcher may legitimately start outside Batch, which is the whole
    point of a role that reaches EC2 directly.
    """
    allowed = instance_types_the_catalog_prices(shipped_catalog())

    assert "g5.2xlarge" in allowed


def test_the_allow_list_is_sorted_and_holds_each_type_once() -> None:
    """Mutation: return a list built by iterating profiles.

    Two profiles may share an instance type, and an IAM condition holding a duplicate is a
    document that differs from the template for no reason anybody can read. Sorting is what
    makes the deployed condition comparable to the rendered one by equality rather than by set
    membership, which is the comparison tests/test_researcher_role_template.py makes.
    """
    allowed = instance_types_the_catalog_prices(shipped_catalog())

    assert list(allowed) == sorted(set(allowed))


def test_the_allow_list_carries_no_wildcard() -> None:
    """Mutation: emit a family pattern such as g5.* instead of the exact type.

    A family wildcard is what got DenyExpensiveGpuAndLargeInstanceFamilies deleted from the
    boundary: it reaches shapes nobody reviewed in one direction and misses p5en, trn2 and the
    metal-48xl sizes in the other. Exact types fail closed on a family AWS ships tomorrow, and
    the cost of failing closed is a one-line request rather than a four-figure line item.
    """
    for instance_type in instance_types_the_catalog_prices(shipped_catalog()):
        assert "*" not in instance_type
        assert "?" not in instance_type


def test_the_allow_list_is_the_catalog_and_not_a_subset_of_it() -> None:
    """Mutation: drop the P-family shapes, which look like the expensive ones to exclude.

    Built here from the catalog by hand rather than by calling the function under test, which
    is the difference between a check and a restatement. The three P shapes are named
    literally because they are the ones somebody tidying would remove.
    """
    catalog = shipped_catalog()
    expected = {profile.instance_type for profile in catalog.compute_profiles}
    allowed = set(instance_types_the_catalog_prices(catalog))

    assert allowed == expected
    assert {"p5.4xlarge", "p4d.24xlarge", "p5.48xlarge"} <= allowed
    assert "g4dn.metal" in allowed


def test_the_shipped_settings_load() -> None:
    """Mutation: leave config/reports/researcher-lane.yaml out of the tree.

    Both the janitor's schedule and the helper's default lifetime are read from this file, so
    an absent one is a Lambda that cannot decide anything and a helper with no default.
    """
    settings = load_lane_settings(PROJECT_ROOT / "config")

    assert settings.warning_lead_minutes > settings.sweep_minutes
    assert settings.default_lifetime_hours > 0


def test_a_warning_lead_shorter_than_the_sweep_is_refused() -> None:
    """Mutation: drop the cross-field check.

    The janitor warns on one sweep and stops on a later one. A lead shorter than the interval
    between sweeps means a machine can expire between two sweeps having never been warned, so
    the first sweep that sees it both warns and is entitled to stop it -- which is the
    behaviour "warns before it stops anything" exists to forbid.
    """
    with pytest.raises(ValidationError, match="warning lead must exceed the sweep interval"):
        LaneSettings.model_validate(
            {
                "schema_version": 1,
                "default_lifetime_hours": 8,
                "warning_lead_minutes": 5,
                "sweep_minutes": 5,
            }
        )
