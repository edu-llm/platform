"""The profile table against the catalogue it is written from."""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import ComputeProfile, WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def render_profile_table():  # type: ignore[no-untyped-def]
    """The tool, imported by path because ``tools/`` is not a package.

    Returns the module already in ``sys.modules`` when there is one, for the reason
    ``tests/module_identity.py`` gives: building a fresh object each call rebinds the name and
    leaves the session holding copies nothing can tell apart.
    """
    cached = sys.modules.get("render_profile_table")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "render_profile_table", PROJECT_ROOT / "tools" / "render_profile_table.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_profile_table"] = module
    spec.loader.exec_module(module)
    return module


def a_profile(
    name: str,
    *,
    provisioned: bool = True,
    rate: str = "1.0060",
    instance_type: str = "g5.xlarge",
) -> ComputeProfile:
    return ComputeProfile(
        name=name,
        instance_type=instance_type,
        accelerator="gpu",
        nodes=1,
        hourly_rate_usd=Decimal(rate),
        pricing_source="a test",
        pricing_observed_at="2026-08-06",
        provisioned=provisioned,
    )


def the_catalogue() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def test_every_profile_in_the_catalogue_reaches_the_table() -> None:
    """Mutation: render only the provisioned ones, or only the first page of them.

    A table that silently drops a row is the failure this tool exists to end, so it is stated
    over the real catalogue rather than over a fixture. A profile added tomorrow appears here
    without anybody editing this test, which is the whole difference from a hand table.
    """
    catalogue = the_catalogue()
    table = render_profile_table().render_table(catalogue.compute_profiles)

    for profile in catalogue.compute_profiles:
        assert f"`{profile.name}`" in table, f"{profile.name} is in the catalogue and not here"


def test_the_order_is_the_catalogues_and_not_an_opinion_about_price() -> None:
    """Mutation: sort by hourly rate, which reads better and moves when a price does.

    The catalogue groups by card family and somebody chose that in review. A renderer that
    reorders makes its output move for a reason its source did not.
    """
    profiles = the_catalogue().compute_profiles
    table = render_profile_table().render_table(profiles)

    positions = [table.index(f"`{profile.name}`") for profile in profiles]
    assert positions == sorted(positions)


def test_an_unprovisioned_profile_says_so_twice() -> None:
    """Mutation: drop the sentence and leave the column, or drop the column.

    A `no` in the fifth column of a wide table is easy to read past, and reading past this one
    costs a queue wait and an approval spent on a refusal. So it is in the row and again in a
    sentence under the table.
    """
    module = render_profile_table()
    table = module.render_table(
        [a_profile("gpu-1xreal"), a_profile("gpu-1xghost", provisioned=False)]
    )

    assert "| `gpu-1xghost` | `g5.xlarge` | 1 | $1.006/hr | no |" in table
    assert "| `gpu-1xreal` | `g5.xlarge` | 1 | $1.006/hr | yes |" in table
    assert "`gpu-1xghost` are in the catalogue and cannot be started today" in table
    assert "gpu-1xreal` are in the catalogue and cannot" not in table


def test_a_catalogue_that_can_all_be_started_gets_no_sentence() -> None:
    """Mutation: always print the sentence, with an empty list in it."""
    table = render_profile_table().render_table([a_profile("gpu-1xreal")])

    assert "cannot be started today" not in table


def test_the_rate_is_the_catalogues_number_and_is_not_rounded_to_the_cent() -> None:
    """Mutation: format the rate to two places, the way the guide prints it.

    Four of the seventeen rates carry four decimals. Rounding them here would make the table
    disagree with the file it was generated from, which is the one thing a generated table is
    for.
    """
    table = render_profile_table().render_table([a_profile("gpu-8xl4", rate="13.3504")])

    assert "$13.3504/hr" in table
    assert "$13.35/hr" not in table


def test_the_check_names_a_profile_a_page_never_mentions() -> None:
    """Mutation: compare counts rather than names, and report a number nobody can act on."""
    module = render_profile_table()
    profiles = [a_profile("gpu-1xsaid"), a_profile("gpu-1xunsaid")]

    assert module.profiles_missing_from("text naming gpu-1xsaid", profiles) == ("gpu-1xunsaid",)
    assert module.profiles_missing_from("gpu-1xsaid and gpu-1xunsaid", profiles) == ()


def test_the_check_exits_one_when_the_page_is_short_of_the_catalogue(tmp_path: Path) -> None:
    """Mutation: return 0 whatever it finds, the way the report tools do.

    Those tools are informational by design and say so. This one is a check, and a check that
    cannot fail is not a check.
    """
    module = render_profile_table()
    page = tmp_path / "guide.md"
    page.write_text("this page mentions gpu-1xt4 and nothing else", encoding="utf-8")

    assert module.main(["--against", str(page)]) == 1


def test_the_check_exits_zero_when_the_page_names_them_all(tmp_path: Path) -> None:
    module = render_profile_table()
    catalogue = the_catalogue()
    page = tmp_path / "guide.md"
    page.write_text(
        " ".join(profile.name for profile in catalogue.compute_profiles), encoding="utf-8"
    )

    assert module.main(["--against", str(page)]) == 0


def test_a_page_that_cannot_be_read_is_two_rather_than_one(tmp_path: Path) -> None:
    """Mutation: let the OSError out, or fold it into the refusal.

    A missing file is the tool being driven wrong and is not a finding about the catalogue.
    Reporting it as 1 would say the page is short of the catalogue when nothing read the page.
    """
    module = render_profile_table()

    assert module.main(["--against", str(tmp_path / "nothing-here.md")]) == 2


def test_rendering_the_real_catalogue_costs_no_argument_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bare form is the one a person runs, so it takes the tree's catalogue by default."""
    module = render_profile_table()

    assert module.main([]) == 0
    printed = capsys.readouterr().out
    assert "| Compute profile | Instance type | Nodes | Rate | Provisioned |" in printed
    assert "`gpu-8xh100`" in printed
