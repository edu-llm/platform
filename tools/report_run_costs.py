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
rather than grouped by the string in the manifest, because the two are not the same list. The
form now offers only declared groups, so nothing new can claim an unbound name, and the
records already written are immutable and outlive a rename: three claim ``tokenizer``, which
is now ``input-core``, and two claim ``evaluation``, which was never declared at all. A bound
team that ran nothing is reported at zero, and spend claiming a team the catalog does not bind
is reported under the name it claimed.

**AND THE SPLIT NOW SAYS HOW APPROXIMATE IT IS, WHICH IT DID NOT HAVE TO BEFORE.** #221
removed the refusal that stopped a submitter claiming a group the roster records them
elsewhere from. It fired inside admission, past the approval gate, so it never prevented any
spend and only ever wasted a lead's signature. What replaced it was ``team_verified`` on the
decision record. Each team's line carries how much of it carries that flag as false, and the
section beneath the split names those runs. It refuses nothing, pages nobody, and moves no
spend between groups.

**IT READS THE FLAG NOW, WHERE IT USED TO WORK THE ANSWER OUT AGAIN.** The first version of
this section compared the submitter on the intent record against ``member_logins`` as the
roster stood when the report ran, which is a different question from the one the record
answers and gave a different answer. Eighteen runs from 2026-08-01 -- every one of them
admitted before any group's membership was written down, when nothing could have been
mis-claimed by anybody -- were named as people charging work to other groups' budgets.
:mod:`edullm_platform.run_costs` argues the reading in full, including why a false flag is
not always a verdict and what this reports for a run that has none.

The third prefix is read for that and for nothing else. ``decision/`` is not in
:data:`LINEAGE_PREFIXES`, which is the pair the audit board's role is granted and the pair
``tools/read_substrate.py`` and ``tools/report_team_assignments.py`` stage; widening that
constant would ask a scheduled reader for a grant it does not hold and does not need.

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
from edullm_platform.contracts.admission import DecisionRecord, IntentRecord
from edullm_platform.contracts.bindings import TeamBindingCatalog
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.run_costs import (
    ContradictedClaim,
    RunCost,
    TeamAttribution,
    TeamSpend,
    UnboundTeamSpend,
    aggregate,
    attribute_to_teams,
    run_costs,
    total_priced,
)

EXIT_OK = 0
EXIT_UNUSABLE = 2

#: The two prefixes every reader of this module's helpers needs. Named rather than globbed
#: over the whole bucket so that a prefix added later is a deliberate edit here, and so that
#: syncing pulls only what is needed rather than every result record as well.
LINEAGE_PREFIXES = ("intent", "attempt")

#: The prefix carrying what admission concluded, which this report reads and the other
#: callers of :data:`LINEAGE_PREFIXES` do not. Held apart from that constant rather than
#: appended to it, because the audit reader role grants ``intent``, ``attempt`` and
#: ``result`` object by object, ``sync_bucket`` raises on a prefix it is refused, and a
#: scheduled board that asks for this one would lose its whole cost mapping over a grant it
#: has no use for.
DECISION_PREFIX = "decision"

#: What this report itself pulls, which is the pair plus the decisions.
REPORT_PREFIXES = (*LINEAGE_PREFIXES, DECISION_PREFIX)


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


def read_decisions(directory: Path) -> tuple[list[DecisionRecord], int]:
    """What admission concluded about each run, counting what would not parse.

    A function of its own rather than a third value out of :func:`read_records`, because
    four callers read that pair and only two of them ask this question. Widening the tuple
    would make ``tools/read_substrate.py``, ``tools/report_team_assignments.py`` and
    ``tools/visibility_board.py`` all name a value they discard.

    An absent ``decision/`` directory raises, exactly as a missing ``intent/`` does. A
    reading that quietly carried on with no verdicts would report every run's team claim as
    unrecorded, which is a real state a store can be in and must not be spelled the same way
    as not having looked.
    """
    decisions: list[DecisionRecord] = []
    unparsed = 0
    for document in _load(directory, DECISION_PREFIX):
        try:
            decisions.append(DecisionRecord.model_validate(document))
        except ValueError:
            unparsed += 1
    return decisions, unparsed


def sync_bucket(
    bucket: str,
    destination: Path,
    *,
    profile: str | None,
    region: str | None,
    prefixes: Sequence[str] = LINEAGE_PREFIXES,
) -> None:
    """Pull each named prefix, refusing to carry on past one that would not come.

    ``prefixes`` defaults to the two this report needs and is a parameter because the
    visibility board reads more of the store than this does -- the result records, for the
    W&B reconciliation, and the binding records, for the account side. One spelling of the
    sync rather than a second one over there: a prefix that raises here and is skipped there
    would be two different meanings for the same denial.

    All or nothing within one call, which is the property the caller depends on. A half
    synced tree reads as records that were never written, and a report that quietly
    described the readable subset is the failure this whole board exists to find. A caller
    that can survive one prefix being refused asks for it in a call of its own.
    """
    for prefix in prefixes:
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


def _leads(lead_logins: Sequence[str]) -> str:
    """Who a team's line names, or that nobody has said.

    A BLANK WHERE A NAME BELONGS READS AS A LOOKUP THAT BROKE, which is the same reason
    ``submission._routing_note`` says "No lead is recorded" rather than leaving the space
    empty. This line read ``led by )`` for any team with none -- ``scratch`` on the shipped
    roster, which is the bin the guide tells every new person to pick and so the team most
    likely to be on a report at all.

    ``lead_logins`` could not be empty when this was written: ``TeamBinding`` required at
    least one login until 2026-08-01, when the constraint was dropped because a group with
    no recorded lead is an ordinary state that the approver page already handled. The
    contract changed and this renderer did not.
    """
    return f"led by {', '.join(lead_logins)}" if lead_logins else "no lead recorded"


def _bound_line(spend: TeamSpend) -> str:
    line = (
        f"- {spend.team_id} (@{spend.github_team_slug}, {_leads(spend.lead_logins)}"
        f"): ${_plain(spend.cost_usd)} across "
        f"{_runs(spend.runs, spend.unpriced_runs)}"
    )
    tags = ", ".join(f"{tag.key}={tag.value}" for tag in spend.attribution_tags)
    if tags:
        line = f"{line} [{tags}]"
    if spend.contradicted_runs:
        # On the team's own line rather than only in the section below, because a lead
        # reading their group's figure is the person the doubt is about and they may never
        # scroll. The claimed total is repeated rather than netted off: subtracting would
        # publish a number no record supports.
        line += (
            f". ${_plain(spend.contradicted_cost_usd)} of that, across "
            f"{_runs(spend.contradicted_runs)}, carries a decision record saying the claim "
            "on it was never verified"
        )
    return line


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
    lines.append(
        "Which group a run is charged to is the group its manifest claimed, and since "
        "2026-08-05 nothing on the platform refuses a claim the roster disagrees with. So "
        "these figures are what each group was charged rather than what each group ran, and "
        "the gap between the two is read out of the decision records below rather than left "
        "to be assumed."
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
            "about or a group that has since been renamed, and the record naming it cannot "
            "be edited. It is "
            "listed here rather than folded into a bound team, because reading one group's "
            "spend as another's would be worse than reading it as nobody's."
        )
        lines.append("")
        lines += [_unbound_line(spend) for spend in attribution.unbound]
        lines.append("")

    lines += _contradicted_lines(attribution)
    return lines


def _contradicted_line(claim: ContradictedClaim) -> str:
    figure = "no figure" if claim.cost_usd is None else f"${_plain(claim.cost_usd)}"
    line = (
        f"- `{claim.run_id}` {figure}: {claim.submitter} claimed {claim.claimed_team} and "
        "its decision record carries `team_verified: false`"
    )
    if claim.recorded_teams:
        # Present tense and separated from the verdict, because it is the one thing on this
        # line a roster edit can change. It is here to answer "who do I ask" and not to
        # establish anything.
        line += f". The roster today records them on {', '.join(claim.recorded_teams)}"
    return line


def _without_verdict_lines(attribution: TeamAttribution) -> list[str]:
    """How many runs above this section is deliberately saying nothing about.

    Printed whether or not anything was contradicted, because the whole finding of this
    section is what the records say and "most of them say nothing" is the larger half of
    that. It is also the sentence that stops the empty case reading as a clean bill: with no
    contradicted runs and no line here, a reader would take the split as fully verified.
    """
    if not attribution.without_verdict:
        return []
    counted = attribution.without_verdict
    return [
        (
            f"{_runs(counted)} above {'carries' if counted == 1 else 'carry'} no verdict "
            "either way and are reported as neither. `team_verified` is false both for a "
            "claim the roster contradicted and for one nothing was in a position to check, "
            "and until a submitter's group was written down every record they left said "
            "false and meant nothing by it. `src/edullm_platform/run_costs.py` says how the "
            "two are told apart and why a run sealed before its submitter's membership was "
            "recorded gets no answer rather than the wrong one."
        ),
        "",
    ]


def _contradicted_lines(attribution: TeamAttribution) -> list[str]:
    """The runs that make the split above approximate, named once and refusing nothing.

    Named here and nowhere else. ``tools/report_spend.py`` prints the same figure beside the
    same split because it is the same split, and it does not repeat this list: a reader who
    wants the runs comes here, and one fact enumerated in two reports is two reports to keep
    in step.
    """
    if not attribution.contradicted and not attribution.without_verdict:
        return []
    lines = ["### What the decision records say about the group each run claimed", ""]
    if not attribution.contradicted:
        lines += [
            (
                "No run above carries a decision record saying its team claim was "
                "contradicted."
            ),
            "",
        ]
        return lines + _without_verdict_lines(attribution)

    counted = len(attribution.contradicted)
    lines += [
        (
            f"{_runs(counted)} above, carrying "
            f"${_plain(attribution.contradicted_cost_usd)}, "
            f"{'was' if counted == 1 else 'were'} admitted with `team_verified: false` on "
            "the decision record, meaning admission compared the group claimed against the "
            "group the submitter was recorded on and they were not the same. Until "
            "2026-08-05 admission refused these, from inside AWS and after a lead had "
            "already released the run, so it never prevented the spend and only ever wasted "
            "the approval. The refusal is gone and the flag is what replaced it. Nothing "
            "here stops a run, and each one is still counted in its claimed team's total "
            "above, because moving it would attribute spend to a group that did not ask "
            "for it."
        ),
        "",
        (
            "It is a floor. `edullm check` still refuses this locally, before anything is "
            "spent, which is the cheap place to ask."
        ),
        "",
        *[_contradicted_line(claim) for claim in attribution.contradicted],
        "",
    ]
    return lines + _without_verdict_lines(attribution)


def render(costs: Sequence[RunCost], *, teams: TeamBindingCatalog, unparsed: int) -> str:
    lines = ["# What runs have cost", ""]
    lines.append(
        "Compute at the catalog's published rate, measured from the attempt records this "
        "platform wrote. A figure here is the container's own wall clock at that rate and "
        "nothing else: not instance start-up, not the minutes an instance stays warm "
        "afterwards, not storage and not transfer."
    )
    lines.append("")
    lines.append(
        "**No figure below is what AWS charged, and it is not a floor on it or a ceiling on "
        "it either.** A short run reads as almost free because the instance under it is not "
        "counted. `run_019fd2fa-5a1e-709c-9181-6a4dffd364e6` ran for 0.215 seconds and is "
        "priced at $0.00 here; the `g4dn.xlarge` behind it was alive for four minutes and "
        "seventeen seconds, which is about $0.04 at the same rate, so this report prices "
        "under a thousandth of the instance time that run occupied. In the other direction "
        "the catalog rate is a list rate this account is not billed: on 2026-08-04 the "
        "figures below total $603.97 of compute against $434.25 of amortised EC2 charge for "
        "the whole account, most families being covered and `p4d.24xlarge` billing at less "
        "than half its catalog rate. What AWS charged is the `Amazon EC2 - Compute` line in "
        "Cost Explorer, which this account can read."
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
    source.add_argument(
        "--bucket", help="sync this bucket's intent/, attempt/ and decision/ prefixes first"
    )
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
                    arguments.bucket,
                    root,
                    profile=arguments.profile,
                    region=arguments.region,
                    prefixes=REPORT_PREFIXES,
                )
            else:
                root = arguments.lineage_root
            intents, attempts, unparsed = read_records(root)
            decisions, unparsed_decisions = read_decisions(root)
            unparsed += unparsed_decisions
            catalog = load_yaml(arguments.config_dir / "workload-catalog.yaml", WorkloadCatalog)
            organization = load_yaml(
                arguments.config_dir / "organization.yaml", OrganizationInventory
            )
        except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return EXIT_UNUSABLE

    costs = run_costs(
        intents=intents,
        attempts=attempts,
        compute_profiles=catalog.compute_profiles,
        decisions=decisions,
    )
    report = render(costs, teams=organization.team_bindings, unparsed=unparsed)

    if arguments.output:
        arguments.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
