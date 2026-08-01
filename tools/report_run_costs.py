"""What every run cost, by team and by person, derived from this platform's own lineage.

The reasoning for computing this ourselves rather than reading it out of AWS is in
:mod:`edullm_platform.run_costs`. The short version is that cost allocation tags cannot be
activated from a linked account, and would not backfill if they could.

Two ways to point it at the records. ``--lineage-root`` reads a directory that already
holds them, which is what a test does and what anybody re-running a report on a downloaded
copy should do. ``--bucket`` syncs the bucket into a temporary directory first, through the
AWS CLI, because ``boto3`` is deliberately not a dependency of this project -- it is
present in the Lambda runtime and nowhere else.

The per-team section is reconciled against the team bindings in ``config/organization.yaml``
rather than grouped by the string in the manifest, because that string is a free-text form
field nothing validates. A bound team that ran nothing is reported at zero, and spend
claiming a team the catalog does not bind is reported under the name it claimed.

Exit codes follow the repository's convention: 0 reported, 2 the inputs could not be read.
There is no 1, because this tool judges nothing and so has nothing to refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.capture_tooling import CaptureFailedError, aws
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.bindings import TeamBindingCatalog
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.run_costs import (
    RunCost,
    TeamSpend,
    UnboundTeamSpend,
    aggregate,
    attribute_to_teams,
    run_costs,
    total_priced,
)

EXIT_OK = 0
EXIT_UNUSABLE = 2

#: The two prefixes this report reads. Named rather than globbed over the whole bucket so
#: that a prefix added later is a deliberate edit here, and so that syncing pulls only what
#: is needed rather than every result and decision record as well.
LINEAGE_PREFIXES = ("intent", "attempt")


class ReportInputError(Exception):
    """The records could not be read, which is never the same as there being none."""


def _load(directory: Path, prefix: str) -> list[object]:
    """Every stored document under one prefix, unwrapped but not yet judged.

    A record is sometimes stored as a JSON string holding JSON, because the state machine
    writes the handler's canonical bytes rather than re-encoding them, and both spellings
    are in the committed fixtures for the same prefix. Unwrapping happens here so that the
    caller sees one shape.

    Anything that is still not an object after unwrapping is returned rather than dropped.
    This used to keep only ``dict`` and discard the rest in silence, so a string-wrapped
    record was not priced, not reported unpriced and not counted as unparsed: it left the
    report entirely, and the count that exists to make that impossible never moved. One of
    the five committed Phase 2 intent records is stored that way.
    """
    root = directory / prefix
    if not root.is_dir():
        raise ReportInputError(f"no {prefix}/ directory under {directory}")
    documents: list[object] = []
    for path in sorted(root.rglob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ReportInputError(f"{path} is not readable JSON: {error}") from error
        if isinstance(loaded, str):
            try:
                loaded = json.loads(loaded)
            except ValueError:
                # Left as the string it is. The caller counts it as unparsed, which is
                # true of it, where raising here would fail the whole report over one
                # record and lose the ninety-nine that are readable.
                pass
        documents.append(loaded)
    return documents


def read_records(directory: Path) -> tuple[list[IntentRecord], list[SchedulerAttempt], int]:
    """Parse both record kinds through their contracts, counting what would not parse.

    Refused records are counted rather than dropped silently. A lineage store that starts
    producing documents this tree cannot read is a defect in the recorder, and a report
    that quietly described the readable subset would hide exactly that.

    ``model_validate`` is handed whatever the store held, including something that is not
    an object at all. Pydantic refuses that with a ``ValidationError``, which is a
    ``ValueError``, so it lands in the count rather than needing a shape check here that
    would have to decide on its own what to do with it.
    """
    intents: list[IntentRecord] = []
    attempts: list[SchedulerAttempt] = []
    unparsed = 0

    for document in _load(directory, "intent"):
        try:
            intents.append(IntentRecord.model_validate(document))
        except ValueError:
            unparsed += 1
    for document in _load(directory, "attempt"):
        try:
            attempts.append(SchedulerAttempt.model_validate(document))
        except ValueError:
            unparsed += 1

    return intents, attempts, unparsed


def sync_bucket(bucket: str, destination: Path, *, profile: str | None, region: str | None) -> None:
    for prefix in LINEAGE_PREFIXES:
        completed = aws(
            ["s3", "sync", f"s3://{bucket}/{prefix}/", str(destination / prefix), "--quiet"],
            profile=profile,
            region=region,
        )
        if completed.returncode != 0:
            raise ReportInputError(f"could not read s3://{bucket}/{prefix}/")


def _plain(value: Decimal) -> str:
    return f"{value:.2f}"


def _runs(runs: int, unpriced: int = 0) -> str:
    """How many runs a line covers, saying out loud where part of it carries no figure."""
    counted = f"{runs} run{'' if runs == 1 else 's'}"
    return f"{counted}, {unpriced} with no figure" if unpriced else counted


def _bound_line(spend: TeamSpend) -> str:
    line = (
        f"- {spend.team_id} (@{spend.github_team_slug}, led by "
        f"{', '.join(spend.lead_logins)}): ${_plain(spend.cost_usd)} across "
        f"{_runs(spend.runs, spend.unpriced_runs)}"
    )
    tags = ", ".join(f"{tag.key}={tag.value}" for tag in spend.attribution_tags)
    return f"{line} [{tags}]" if tags else line


def _unbound_line(spend: UnboundTeamSpend) -> str:
    return (
        f"- `{spend.claimed_team}`: ${_plain(spend.cost_usd)} across "
        f"{_runs(spend.runs, spend.unpriced_runs)}"
    )


def by_team(costs: Sequence[RunCost], teams: TeamBindingCatalog) -> list[str]:
    attribution = attribute_to_teams(costs, catalog=teams)
    lines = ["## By team", ""]
    lines.append(
        "Rolled up against the team bindings in `config/organization.yaml`, so a team here "
        "is one this platform has been told about rather than whatever was typed into the "
        "submission form. A bound team that ran nothing appears at zero, because a group "
        "having gone quiet is worth reading."
    )
    lines.append("")
    if attribution.bound:
        lines += [_bound_line(spend) for spend in attribution.bound]
    else:
        lines.append(
            "`config/organization.yaml` binds no teams, so there is nothing to roll up "
            "against and every run's spend is below under the name it claimed."
        )
    lines.append("")

    if attribution.unbound:
        lines.append("### Claimed against a team nothing binds")
        lines.append("")
        lines.append(
            "Nothing in the binding catalog carries these names, so the "
            f"${_plain(attribution.unbound_cost_usd)} across "
            f"{_runs(attribution.unbound_runs)} beneath them cannot be routed to a lead or "
            "to a cost centre. Each name is either a group the roster has not been told "
            "about or a misspelling in the submission form's free-text team box. It is "
            "listed here rather than folded into a bound team, because reading one group's "
            "spend as another's would be worse than reading it as nobody's."
        )
        lines.append("")
        lines += [_unbound_line(spend) for spend in attribution.unbound]
        lines.append("")
    return lines


def render(costs: Sequence[RunCost], *, teams: TeamBindingCatalog, unparsed: int) -> str:
    lines = ["# What runs have cost", ""]
    lines.append(
        "Compute at the catalog's published rate, measured from the attempt records this "
        "platform wrote. It excludes instance start-up, idle time, storage and transfer, "
        "so it is what the work cost rather than what AWS charged."
    )
    lines.append("")

    lines += by_team(costs, teams)

    lines.append("## By submitter")
    lines.append("")
    for who, spend in sorted(aggregate(costs, key="submitter").items(), key=lambda kv: -kv[1]):
        lines.append(f"- {who}: ${_plain(spend)}")
    lines.append("")

    unpriced = [entry for entry in costs if not entry.priced]
    if unpriced:
        lines.append("## Runs with no figure, and why")
        lines.append("")
        for entry in unpriced:
            lines.append(f"- `{entry.run_id}` ({entry.compute_profile}): {entry.unpriced_reason}")
        lines.append("")

    priced = [entry for entry in costs if entry.priced]
    lines.append("## Every run")
    lines.append("")
    for entry in sorted(priced, key=lambda item: item.cost_usd or Decimal(0), reverse=True):
        assert entry.cost_usd is not None
        lines.append(
            f"- `{entry.run_id}` {entry.team}/{entry.submitter} "
            f"{entry.compute_profile} {entry.seconds:.1f}s "
            f"across {entry.attempts} attempt(s): ${_plain(entry.cost_usd)}"
        )
    lines.append("")

    lines.append(
        f"**{len(priced)} runs priced, {len(unpriced)} not, "
        f"${_plain(total_priced(costs))} total.**"
    )
    if unparsed:
        lines.append("")
        lines.append(
            f"**{unparsed} record{'' if unparsed == 1 else 's'} did not parse against the "
            "contracts in this tree, and so {} left out of everything above.** A stored "
            "record the current tree cannot read means a contract was tightened after that "
            "record was written. The record is not wrong -- it was valid when it was "
            "sealed, and it is immutable -- so what needs deciding is whether the rule that "
            "now refuses it should tolerate what came before it.".format(
                "is" if unparsed == 1 else "are"
            )
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--lineage-root", type=Path, help="a directory already holding records")
    source.add_argument("--bucket", help="sync this bucket's intent/ and attempt/ prefixes first")
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument("--output", type=Path, help="write the report here rather than to stdout")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    with tempfile.TemporaryDirectory() as scratch:
        try:
            if arguments.bucket:
                root = Path(scratch)
                sync_bucket(
                    arguments.bucket, root, profile=arguments.profile, region=arguments.region
                )
            else:
                root = arguments.lineage_root
            intents, attempts, unparsed = read_records(root)
            catalog = load_yaml(arguments.config_dir / "workload-catalog.yaml", WorkloadCatalog)
            organization = load_yaml(
                arguments.config_dir / "organization.yaml", OrganizationInventory
            )
        except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return EXIT_UNUSABLE

    costs = run_costs(
        intents=intents, attempts=attempts, compute_profiles=catalog.compute_profiles
    )
    report = render(costs, teams=organization.team_bindings, unparsed=unparsed)

    if arguments.output:
        arguments.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
