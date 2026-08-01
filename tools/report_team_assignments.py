"""What a research-group assignment would rest on, per person, with the evidence beside it.

**Nothing anywhere records which research group a person belongs to.**
``config/organization.yaml`` declares six teams and every ``member_logins`` is empty, and
the comment above them says why: a person filed under the wrong group is indistinguishable
from one filed correctly, and the reader placed to notice is the person whose work was
attributed elsewhere. So the gap is filled one line at a time by each group's lead, and what
a lead needs in order to write their line is the evidence, not a guess.

This assembles that evidence. It proposes a team only where a signal **names** one, and it
grades what it proposes by how the name was arrived at.

**A signal names a team or it does not, and this file never bridges the difference.** A
lineage record carries ``manifest.team``. An S3 output prefix is ``teams/<team>/runs/...``.
A GitHub team has a slug. A repository or a directory can be called ``memory-split``. Each
of those is a literal string compared with a declared ``team_id``. What this deliberately
does not hold is a map from research topic to team -- that mamba is memory, that a
scaffolding experiment is curriculum, that a FLORES sweep is tokenizer. Those readings may
all be right and none of them is written down anywhere this could read, so encoding them
here would turn one person's inference into a report that looks like a record.

Somebody active in a repository no team name matches is therefore reported at ``none`` with
the repository listed beside them. That is the useful answer: it says the person is working
and that nothing this can read places them, which is exactly the case a lead has to settle
from memory.

**Why Weights and Biases is not one of the sources.** The obvious query is which projects a
person has logged runs to. It does not answer this question reliably, and the counter-example
is in the account: ``eric-gpu-access-smoke`` was requested by ``ericrcwu001`` and is logged
under ``amy-lin-alpha-ai``. Runs submitted through a shared cluster carry the W&B identity of
whichever key was on the node, so a project-to-person mapping read out of W&B reports the key
holder and cannot be told apart from one that reports the researcher. Lineage records carry a
submitter this platform recorded itself, which is the same evidence without the ambiguity.

Exit codes follow the repository's convention: 0 reported, 2 the inputs could not be read.
There is no 1. This proposes and grades; it decides nothing, and applying an assignment is a
reviewed edit to ``config/organization.yaml`` that no tool in this repository makes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from report_run_costs import LINEAGE_PREFIXES, ReportInputError, read_records

from edullm_platform.capture_tooling import CaptureFailedError, aws, observed_now
from edullm_platform.config import load_yaml
from edullm_platform.contracts.bindings import normalize_github_login
from edullm_platform.contracts.inventory import OrganizationInventory

EXIT_OK = 0
EXIT_UNUSABLE = 2

DEFAULT_ORGANIZATION = "edu-llm"
DEFAULT_LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"
DEFAULT_OUTPUTS_BUCKET = "sbsandbox-intern-edullm-outputs"

#: Where a run's artifacts land, as ``contracts/results.py`` composes it. Matched rather
#: than composed here, so this reads the layout that exists instead of asserting one.
OUTPUTS_TEAM_PREFIX = "teams/"

__all__ = [
    "Assignment",
    "Confidence",
    "Evidence",
    "ReportInputError",
    "Signal",
    "assignments",
    "build_parser",
    "gather",
    "main",
    "parse_evidence",
    "render",
    "render_csv",
]


class Confidence(StrEnum):
    """How a proposed team was arrived at, in the three bands the draft is read in.

    ``STRONG`` is a record of the person and the team together: they submitted a run under
    it, or a roster or a GitHub team lists them in it. ``WEAK`` is a name match on work they
    authored, which says what they have been near rather than which group they are in.
    ``NONE`` is no team named at all, and is a real answer rather than a missing one.
    """

    STRONG = "strong"
    WEAK = "weak"
    NONE = "none"


#: Worst last. Used to order people in the report and to pick the band for a person holding
#: signals of more than one kind, which is always the best signal they have.
_BANDS = (Confidence.STRONG, Confidence.WEAK, Confidence.NONE)

#: Which source settles it when two of them name different teams, most settled first.
#:
#: The roster is a decision somebody made and the rest are observations, so a group a lead
#: has already written down is not redrafted from runs that may predate it. A GitHub team is
#: the same kind of decision made in the organization settings. Below those, what a run's
#: manifest claimed comes before where its bytes landed, because the prefix is derived from
#: the claim and agreeing with it twice is not two pieces of evidence.
_SOURCE_ORDER = ("roster", "github-team", "lineage", "outputs", "repository", "path")


@dataclass(frozen=True)
class Signal:
    """One reason to think somebody belongs to a group, and how good a reason it is.

    ``team_id`` is ``None`` when the signal found the person and named no declared team. It
    is kept rather than dropped because it is the difference between a person nothing knows
    about and a person whose work this cannot place, and those need different answers from
    whoever reads the draft.
    """

    source: str
    team_id: str | None
    confidence: Confidence
    detail: str
    #: How much of this signal there is, which for a run record is how many runs. One by
    #: default, because most sources say a thing once. It settles nothing on its own and is
    #: only read to choose between two teams the same source named.
    weight: int = 1


@dataclass(frozen=True)
class Assignment:
    """The draft line for one person, and everything it was drawn from."""

    github_login: str
    display_name: str | None
    team_id: str | None
    confidence: Confidence
    signals: tuple[Signal, ...]

    @property
    def placed(self) -> tuple[Signal, ...]:
        return tuple(signal for signal in self.signals if signal.team_id is not None)

    @property
    def unplaced(self) -> tuple[Signal, ...]:
        return tuple(signal for signal in self.signals if signal.team_id is None)

    @property
    def evidence(self) -> str:
        """One line saying what this rests on, for the CSV column of the same name.

        Every signal rather than only the ones that carried the verdict. A weak proposal
        beside three repositories that name nothing is a different thing to correct than a
        weak proposal standing alone, and the difference is invisible if the report keeps
        only the signal that won.
        """
        if not self.signals:
            return "no signal in any source"
        return "; ".join(signal.detail for signal in self.signals)


@dataclass(frozen=True)
class Evidence:
    """The facts this report cannot hold, as the gather mode wrote them down.

    Everything here is a statement about the moment it was taken. GitHub team membership and
    repository access are changed in a browser and leave no artifact, and the buckets are
    appended to by every run, so a report built from a stale file describes a stale
    organization.
    """

    observed_at: str
    organization: str
    #: Slug to logins, for the GitHub teams the roster's bindings declare. A membership here
    #: names a team outright, which is why it grades strong even though it is not a run.
    github_team_members: Mapping[str, frozenset[str]]
    #: Team id to the run ids found under ``teams/<team>/runs/`` in the outputs bucket. On
    #: its own it names no person; it is joined to a lineage record's submitter by run id.
    output_prefix_runs: Mapping[str, frozenset[str]]
    #: Repository name to the logins that have committed to it, over every branch rather
    #: than the default one, because a research branch is where most of this work lives.
    repository_contributors: Mapping[str, frozenset[str]]
    #: Repository name to the logins that have pushed a branch to it.
    #:
    #: NOT A DUPLICATE OF THE LINE ABOVE, AND THE PEOPLE IT ADDS ARE THE POINT. A commit
    #: authored from a laptop whose git email belongs to no GitHub account resolves to no
    #: login, so its author is invisible to the contributor list however much they wrote.
    #: Two people in this organization are in exactly that position, and both of them are
    #: named here because GitHub records who pushed even when it cannot say who wrote.
    repository_push_actors: Mapping[str, frozenset[str]]
    #: Login to the directory path segments they have authored under, per repository, as
    #: ``"<repository>:<segment>"``. Segments and never file names: ``data/tokenizer.py``
    #: is not evidence about the tokenizer group and ``memory-split/`` is.
    authored_path_segments: Mapping[str, frozenset[str]]


def _logins(values: object, *, field: str) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ReportInputError(f"{field} must be a list of GitHub logins")
    return frozenset(normalize_github_login(str(item)) for item in values)


def _strings(values: object, *, field: str) -> frozenset[str]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ReportInputError(f"{field} must be a list of strings")
    return frozenset(str(item) for item in values)


def _mapping(document: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = document[key]
    if not isinstance(value, dict):
        raise ReportInputError(f"{key} must be an object")
    return value


def parse_evidence(document: object) -> Evidence:
    """Read the gathered document, refusing what it cannot read rather than defaulting.

    A missing key is refused instead of read as an empty answer. Empty is the loudest thing
    this report can be told -- it says nobody has run anything and nobody has committed
    anything, which puts every person in the draft at ``none`` -- and a typo in a key name
    would say exactly that while meaning nothing.
    """
    if not isinstance(document, dict):
        raise ReportInputError("the gathered evidence must be a JSON object")

    required = (
        "observed_at",
        "organization",
        "github_team_members",
        "output_prefix_runs",
        "repository_contributors",
        "repository_push_actors",
        "authored_path_segments",
    )
    absent = [key for key in required if key not in document]
    if absent:
        raise ReportInputError(
            f"the gathered evidence is missing {', '.join(absent)}. Re-gather with --gather "
            "rather than filling the keys in by hand, so what the report reads is what the "
            "organization and the buckets said."
        )

    return Evidence(
        observed_at=str(document["observed_at"]),
        organization=str(document["organization"]),
        github_team_members={
            str(slug).casefold(): _logins(logins, field=f"github_team_members.{slug}")
            for slug, logins in _mapping(document, "github_team_members").items()
        },
        output_prefix_runs={
            str(team): _strings(runs, field=f"output_prefix_runs.{team}")
            for team, runs in _mapping(document, "output_prefix_runs").items()
        },
        repository_contributors={
            str(name): _logins(logins, field=f"repository_contributors.{name}")
            for name, logins in _mapping(document, "repository_contributors").items()
        },
        repository_push_actors={
            str(name): _logins(logins, field=f"repository_push_actors.{name}")
            for name, logins in _mapping(document, "repository_push_actors").items()
        },
        authored_path_segments={
            normalize_github_login(str(login)): _strings(
                segments, field=f"authored_path_segments.{login}"
            )
            for login, segments in _mapping(document, "authored_path_segments").items()
        },
    )


def read_evidence(path: Path) -> Evidence:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReportInputError(f"{path} could not be read: {error}") from error
    except ValueError as error:
        raise ReportInputError(f"{path} is not readable JSON: {error}") from error
    try:
        return parse_evidence(document)
    except ReportInputError as error:
        raise ReportInputError(f"{path}: {error}") from error


@dataclass(frozen=True)
class SubmittedRun:
    """A run this platform recorded, reduced to the two fields that matter here."""

    run_id: str
    submitter: str
    team: str


def submitted_runs(intents: Iterable[Any]) -> tuple[SubmittedRun, ...]:
    """Every intent record as a submitter and the team they claimed.

    Read through ``tools/report_run_costs.py`` rather than by a second parser written here.
    That one already validates against ``IntentRecord`` and counts what will not validate,
    and a record the current contract refuses is still a record of a person running under a
    group -- so the count matters to the reader and the parsing decision must not be made
    twice.
    """
    return tuple(
        SubmittedRun(
            run_id=intent.run_id, submitter=intent.submitter, team=intent.manifest.team
        )
        for intent in intents
    )


def _lineage_signals(
    login: str, runs: Sequence[SubmittedRun], declared: Mapping[str, str]
) -> list[Signal]:
    """What this person has run, and under which name.

    A claimed team the bindings do not carry is reported rather than dropped. It is a
    finding about the roster, or about a group that has been renamed since the record was
    written, and the person who claimed it is the person whose group is being decided.
    """
    wanted = normalize_github_login(login)
    mine = [run for run in runs if normalize_github_login(run.submitter) == wanted]
    counted: dict[str, int] = {}
    for run in mine:
        counted[run.team] = counted.get(run.team, 0) + 1

    signals: list[Signal] = []
    for team, count in sorted(counted.items(), key=lambda item: (-item[1], item[0])):
        plural = "" if count == 1 else "s"
        if team in declared:
            signals.append(
                Signal(
                    source="lineage",
                    team_id=declared[team],
                    confidence=Confidence.STRONG,
                    detail=f"submitted {count} run{plural} under team `{team}`",
                    weight=count,
                )
            )
        else:
            signals.append(
                Signal(
                    source="lineage",
                    team_id=None,
                    confidence=Confidence.NONE,
                    detail=(
                        f"submitted {count} run{plural} claiming `{team}`, which "
                        "`team_bindings` does not declare"
                    ),
                )
            )
    return signals


def _output_prefix_signals(
    login: str,
    runs: Sequence[SubmittedRun],
    evidence: Evidence,
    declared: Mapping[str, str],
) -> list[Signal]:
    """Which team's prefix this person's runs actually wrote under.

    Held apart from the lineage signal even though it usually agrees with it, because the
    two are different claims. The lineage record is what the submitter asked for; the prefix
    is where the bytes went. A run whose artifacts landed under a team its manifest does not
    name is a fault in the execution path and reads here as a second team for one person,
    which is the shape that gets it looked at.
    """
    wanted = normalize_github_login(login)
    mine = {run.run_id for run in runs if normalize_github_login(run.submitter) == wanted}
    signals: list[Signal] = []
    for team, run_ids in sorted(evidence.output_prefix_runs.items()):
        matched = mine & set(run_ids)
        if not matched or team not in declared:
            continue
        signals.append(
            Signal(
                source="outputs",
                team_id=declared[team],
                confidence=Confidence.STRONG,
                detail=f"{len(matched)} of their runs wrote under `teams/{team}/runs/`",
                weight=len(matched),
            )
        )
    return signals


def _roster_signals(
    login: str, inventory: OrganizationInventory
) -> list[Signal]:
    """What ``config/organization.yaml`` already says, which is the whole point of the draft.

    Empty for everybody today. It is read anyway and read first, so that a group filled in
    by its lead stops being redrafted from weaker evidence the next time this runs.
    """
    return [
        Signal(
            source="roster",
            team_id=team.team_id,
            confidence=Confidence.STRONG,
            detail=f"`config/organization.yaml` lists them in `{team.team_id}`",
        )
        for team in inventory.teams_for_member(login)
    ]


def _github_team_signals(
    login: str, inventory: OrganizationInventory, evidence: Evidence
) -> list[Signal]:
    """Membership of the GitHub team a binding names, which names a team outright.

    Not a run, and graded strong all the same. Somebody put this person in a group in the
    organization settings, which is a record of the same fact the roster is missing, made by
    a person rather than inferred from a file name.
    """
    wanted = normalize_github_login(login)
    signals: list[Signal] = []
    for team in inventory.team_bindings.teams:
        members = evidence.github_team_members.get(team.github_team_slug.casefold())
        if members is None or wanted not in members:
            continue
        signals.append(
            Signal(
                source="github-team",
                team_id=team.team_id,
                confidence=Confidence.STRONG,
                detail=f"a member of the GitHub team `{team.github_team_slug}`",
            )
        )
    return signals


#: What may follow a team id in a name that is still that team's name. A repository called
#: ``Memory-Split-P3`` is the memory-split group's third project.
#:
#: A hyphen and a slash and deliberately not an underscore. Team ids are kebab-case by
#: contract, so a hyphen is how a name composed out of one is spelled, and ``tokenizer_utils``
#: is snake_case, which is how a Python module is spelled. Admitting the underscore places
#: everybody who has edited a tokenizer helper in the tokenizer group.
_NAME_SEPARATORS = ("-", "/")


def _name_matched(name: str, declared: Mapping[str, str]) -> str | None:
    """The declared team a name is, folded, or ``None``.

    Either the whole name is the team id, or the team id is followed by a separator and
    something else. NEVER A SUBSTRING, and the difference is what keeps this rule mechanical
    rather than a judgement. ``data/tokenizer.py`` ends in the team id and is a file called
    tokenizer.py that half the repository has edited; ``edullm-token-selection`` contains
    neither ``tokenizer`` nor a hyphen in the right place and is about which tokens a
    training step keeps rather than about a tokenizer. A contains rule grades both as
    evidence about the tokenizer group.

    What survives is a name somebody chose that begins with a group's name. That is still
    only weak evidence: it says the work was filed under the group, not that this person is
    in it.
    """
    folded = name.casefold()
    if folded in declared:
        return declared[folded]
    for team_id, declared_as in declared.items():
        if any(folded.startswith(team_id + separator) for separator in _NAME_SEPARATORS):
            return declared_as
    return None


def _authorship_signals(
    login: str, evidence: Evidence, declared: Mapping[str, str]
) -> list[Signal]:
    """Repositories and directories this person has written in, graded on their names.

    Two outcomes and both are reported. A repository or a path segment whose name is a
    declared team id is weak evidence for that team: somebody chose the name and the person
    worked under it, which is a good deal less than a run record and a good deal more than
    nothing. Anything else names no team, and is listed so the reader can see the person is
    active before deciding that nothing places them.
    """
    wanted = normalize_github_login(login)
    signals: list[Signal] = []

    #: Wrote and pushed are two ways of having worked somewhere, and the report says which.
    #: Somebody who only pushed is somebody whose commits GitHub could not attribute, which
    #: is worth reading as itself rather than flattened into the other.
    worked_in = {
        repository: (
            wanted in evidence.repository_contributors.get(repository, frozenset()),
            wanted in evidence.repository_push_actors.get(repository, frozenset()),
        )
        for repository in sorted(
            set(evidence.repository_contributors) | set(evidence.repository_push_actors)
        )
    }

    unplaced_repositories: list[str] = []
    for repository, (wrote, pushed) in worked_in.items():
        if not (wrote or pushed):
            continue
        matched = _name_matched(repository, declared)
        if matched is None:
            unplaced_repositories.append(repository)
            continue
        did = "commits" if wrote else "pushed branches"
        signals.append(
            Signal(
                source="repository",
                team_id=matched,
                confidence=Confidence.WEAK,
                detail=f"{did} in `{repository}`, whose name is the team id",
            )
        )

    unplaced_paths: list[str] = []
    for qualified in sorted(evidence.authored_path_segments.get(wanted, frozenset())):
        repository, _, segment = qualified.partition(":")
        matched = _name_matched(segment, declared)
        if matched is None:
            unplaced_paths.append(qualified)
            continue
        signals.append(
            Signal(
                source="path",
                team_id=matched,
                confidence=Confidence.WEAK,
                detail=f"authored files under `{segment}/` in {repository}",
            )
        )

    if unplaced_repositories:
        signals.append(
            Signal(
                source="repository",
                team_id=None,
                confidence=Confidence.NONE,
                detail=(
                    "worked in "
                    + ", ".join(f"`{name}`" for name in unplaced_repositories)
                    + ", which no declared team is named after"
                ),
            )
        )
    if unplaced_paths:
        signals.append(
            Signal(
                source="path",
                team_id=None,
                confidence=Confidence.NONE,
                detail=(
                    f"authored files under {len(unplaced_paths)} directories no declared "
                    "team is named after"
                ),
            )
        )
    return signals


def _verdict(signals: Sequence[Signal]) -> tuple[str | None, Confidence]:
    """The team to draft and the band to draft it in.

    The best band any signal reached, then the most settled source inside that band, then
    the heaviest team that source named, then the team id. Every step is a total order, so
    two readings of the same evidence cannot disagree about the answer.

    A person whose signals point at two teams still gets one line, and the other team is in
    the evidence column beside it. Reporting no team because two were found would hide the
    more interesting fact, which is that somebody has worked under both.
    """
    for band in (Confidence.STRONG, Confidence.WEAK):
        for source in _SOURCE_ORDER:
            weighed: dict[str, int] = {}
            for signal in signals:
                if signal.confidence is band and signal.source == source and signal.team_id:
                    weighed[signal.team_id] = weighed.get(signal.team_id, 0) + signal.weight
            if weighed:
                return min(weighed.items(), key=lambda item: (-item[1], item[0]))[0], band
    return None, Confidence.NONE


def assignments(
    inventory: OrganizationInventory,
    *,
    runs: Sequence[SubmittedRun],
    evidence: Evidence,
    exclude: Sequence[str] = (),
) -> list[Assignment]:
    """One draft line per person on the roster, ordered strong first and none last.

    THE ROSTER AND NOT THE ORGANIZATION. ``tools/report_onboarding_readiness.py`` takes the
    union for a reason that does not apply here: it is looking for people the roster has
    missed, and this is proposing which research group somebody is in. Somebody the roster
    does not name is not in a research group yet, and drafting one for them would propose an
    assignment that cannot be applied -- ``member_logins`` is validated against ``members``.

    ``exclude`` is for the people in the organization who are not researchers. They hold
    real access and appear in real repositories, and a line proposing a group for them is a
    line the reader has to decide about every time this is run.
    """
    excluded = {normalize_github_login(login) for login in exclude}
    declared = {team.team_id.casefold(): team.team_id for team in inventory.team_bindings.teams}

    drafted: list[Assignment] = []
    for member in inventory.members:
        if member.normalized_github_login in excluded:
            continue
        login = member.github_login
        signals = [
            *_roster_signals(login, inventory),
            *_github_team_signals(login, inventory, evidence),
            *_lineage_signals(login, runs, declared),
            *_output_prefix_signals(login, runs, evidence, declared),
            *_authorship_signals(login, evidence, declared),
        ]
        team_id, confidence = _verdict(signals)
        drafted.append(
            Assignment(
                github_login=login,
                display_name=member.display_name,
                team_id=team_id,
                confidence=confidence,
                signals=tuple(signals),
            )
        )

    return sorted(
        drafted,
        key=lambda entry: (
            _BANDS.index(entry.confidence),
            entry.team_id or "",
            entry.github_login.casefold(),
        ),
    )


def _csv_field(value: str) -> str:
    """One CSV field, quoted the way RFC 4180 quotes one.

    Written here rather than through :mod:`csv`, because every field this emits is composed
    above and the whole output is four columns. What it must not do is emit an evidence
    column containing a comma unquoted, which would silently move a column.
    """
    if any(character in value for character in ',"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def render_csv(drafted: Sequence[Assignment]) -> str:
    """The draft as ``login,team,confidence,evidence``, which is what gets corrected."""
    lines = ["login,team,confidence,evidence"]
    lines += [
        ",".join(
            _csv_field(field)
            for field in (
                entry.github_login,
                entry.team_id or "",
                entry.confidence.value,
                entry.evidence,
            )
        )
        for entry in drafted
    ]
    return "\n".join(lines) + "\n"


def _count(number: int, singular: str, plural: str) -> str:
    return f"{number} {singular if number == 1 else plural}"


def _described(entry: Assignment) -> str:
    if entry.display_name is None:
        return f"`{entry.github_login}`"
    return f"{entry.display_name} (`{entry.github_login}`)"


def render(
    drafted: Sequence[Assignment], evidence: Evidence, *, unparsed: int = 0
) -> str:
    banded = {band: [e for e in drafted if e.confidence is band] for band in _BANDS}
    silent = [entry for entry in banded[Confidence.NONE] if not entry.signals]
    unplaced = [entry for entry in banded[Confidence.NONE] if entry.signals]

    lines = [
        "# Draft research-group assignments",
        "",
        (
            f"{_count(len(drafted), 'person', 'people')} on the roster. "
            f"{_count(len(banded[Confidence.STRONG]), 'is', 'are')} placed by a record of "
            f"them and the team together, {_count(len(banded[Confidence.WEAK]), 'is', 'are')} "
            "placed only by a name match on work they authored, and "
            f"{_count(len(banded[Confidence.NONE]), 'is', 'are')} placed by nothing."
        ),
        "",
        (
            "**This is a draft to correct and not a record.** A team here is one some source "
            "named in so many words. Nothing in this report reads a research topic as a "
            "group, so a person working on exactly the thing a group is for appears at "
            "`none` unless a run, a roster, a GitHub team, a bucket prefix or a directory "
            "spelled the group's name. Applying any of it is a reviewed edit to "
            "`member_logins` in `config/organization.yaml`."
        ),
        "",
        (
            f"The organization and the buckets were read at {evidence.observed_at}. "
            "Membership of a GitHub team is changed in a browser and leaves no artifact "
            "here, so re-gather before acting on a report that has been sitting around."
        ),
        "",
    ]

    for band, heading, preamble in (
        (
            Confidence.STRONG,
            "Placed by a record",
            (
                "Each of these is a run this platform recorded under the team, a bucket "
                "prefix their run wrote to, a GitHub team holding them, or a roster line. "
                "The person and the team appear together in something somebody wrote down."
            ),
        ),
        (
            Confidence.WEAK,
            "Placed by a name",
            (
                "A repository or a directory these people have authored in is called what a "
                "declared team is called. That is evidence about what they have worked on "
                "and not a record of which group they are in, so each of these is a question "
                "for the group's lead rather than an answer."
            ),
        ),
    ):
        lines += [f"## {heading}", "", preamble, ""]
        if banded[band]:
            lines += [
                f"- {_described(entry)}: **{entry.team_id}** -- {entry.evidence}"
                for entry in banded[band]
            ]
        else:
            lines.append("Nobody.")
        lines.append("")

    lines += [
        "## Active, and named by nothing",
        "",
        (
            "These people have commits, and every repository and directory they have worked "
            "in is called something no declared team is called. Reading a group off the "
            "subject of the work is exactly the inference this report will not make, so what "
            "they have been near is listed and no team is proposed."
        ),
        "",
    ]
    lines += (
        [f"- {_described(entry)}: {entry.evidence}" for entry in unplaced]
        if unplaced
        else ["Nobody."]
    )
    lines.append("")

    lines += [
        "## No signal in any source",
        "",
        (
            "No run, no bucket prefix, no GitHub team, no roster line and no commit in any "
            "repository this read. Every one of these has to be answered from somebody's "
            "memory, and they are the reason this report exists rather than a gap in it."
        ),
        "",
    ]
    lines += (
        [f"- {_described(entry)}" for entry in silent] if silent else ["Nobody."]
    )
    lines.append("")

    if unparsed:
        lines += [
            (
                f"**{_count(unparsed, 'lineage record', 'lineage records')} did not validate "
                "against the contracts in this tree and is left out of the counts above.** A "
                "stored record the current tree cannot read is a contract tightened after the "
                "record was sealed. The record is not wrong and it is immutable, so it is "
                "counted here rather than treated as absent, and a submitter it names may be "
                "under-evidenced by exactly that much."
            ),
            "",
        ]
    return "\n".join(lines)


#: How many times a call that never reached GitHub is tried again, and how long the first
#: wait is. A gather is tens of thousands of requests over several minutes, so a connection
#: that drops once is the ordinary case rather than an outage. Without this the whole sweep
#: is lost to it, which is what happened the first time this was run.
NETWORK_ATTEMPTS = 4
FIRST_BACKOFF_SECONDS = 2.0


def _github(*arguments: str) -> Any:
    """One ``gh api`` call, parsed, with the service's own words on a failure.

    Not in :mod:`edullm_platform.capture_tooling`, and that module says why it holds no
    GitHub wrapper: what a refused ``gh api`` call prints is the service's stderr, which is
    the whole value of the message to whoever has to fix the session.

    A failure carrying an HTTP status is an answer and is raised at once. GitHub said no,
    and asking again gets the same no more slowly. A failure carrying none never reached
    GitHub, and that one is retried.
    """
    for attempt in range(NETWORK_ATTEMPTS):
        completed = subprocess.run(
            ["gh", "api", *arguments], capture_output=True, text=True, check=False
        )
        if completed.returncode == 0:
            return json.loads(completed.stdout or "null")
        answered = "HTTP " in completed.stderr
        if answered or attempt == NETWORK_ATTEMPTS - 1:
            raise ReportInputError(
                f"gh api {' '.join(arguments)} failed with {completed.returncode}: "
                f"{completed.stderr.strip()[:400]}"
            )
        time.sleep(FIRST_BACKOFF_SECONDS * 2**attempt)
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


def _github_listing(path: str) -> list[Any]:
    """Every page of a listing, flattened.

    ``--paginate`` alone emits one JSON array per page, which is not a document anything can
    parse; ``--slurp`` wraps them into one. The default page size is thirty, so a listing
    read without this reports the people past the cut as having contributed nothing.
    """
    pages = _github("--paginate", "--slurp", path)
    return [entry for page in pages or [] for entry in page or []]


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise ReportInputError(
            f"git {' '.join(arguments)} in {root} failed with {completed.returncode}: "
            f"{completed.stderr.strip()[:400]}"
        )
    return completed.stdout


@dataclass(frozen=True)
class CommitSweep:
    """Three answers read out of one pass over the organization's repositories.

    Who has committed where, who has pushed where, and which GitHub login each commit author
    email belongs to. Gathered together because the sweep is the expensive part -- every
    branch of every repository, paged -- and asking again for each would multiply a call
    count already in the tens of thousands.
    """

    contributors: Mapping[str, frozenset[str]]
    push_actors: Mapping[str, frozenset[str]]
    logins_by_email: Mapping[str, str]


def sweep_commits(organization: str) -> CommitSweep:
    """Every branch of every repository, who authored what, and who pushed it.

    EVERY BRANCH AND NOT THE DEFAULT ONE. The ``contributors`` endpoint counts the default
    branch, and research in this organization lives on branches that were never merged. One
    of them carries six people who appear in no other source at all, and reading the default
    branch reports them as having done nothing.

    THE PUSH ACTOR IS ASKED BECAUSE THE AUTHOR IS SOMETIMES NOBODY. A commit whose git email
    belongs to no GitHub account has no ``author.login`` at all, so its author is invisible
    to the sweep however much they wrote. Inventing a login from the local part of the
    address is how one person's work ends up under another person's name, so that is not
    done; the activity feed says who pushed the branch, which GitHub knows for certain
    because it was an authenticated request.
    """
    contributors: dict[str, frozenset[str]] = {}
    pushers: dict[str, frozenset[str]] = {}
    resolved: dict[str, str] = {}
    for repository in _github_listing(f"orgs/{organization}/repos"):
        name = str(repository["name"])
        logins: set[str] = set()
        for branch in _github_listing(f"repos/{organization}/{name}/branches"):
            for commit in _github_listing(
                f"repos/{organization}/{name}/commits?sha={branch['name']}"
            ):
                author = commit.get("author") or {}
                login = author.get("login")
                if not login:
                    continue
                logins.add(str(login))
                email = ((commit.get("commit") or {}).get("author") or {}).get("email")
                if isinstance(email, str):
                    resolved[email.casefold()] = str(login)
        contributors[name] = frozenset(logins)
        pushers[name] = frozenset(
            str((entry.get("actor") or {}).get("login"))
            for entry in _github_listing(f"repos/{organization}/{name}/activity")
            if (entry.get("actor") or {}).get("login")
        )
    return CommitSweep(
        contributors=contributors, push_actors=pushers, logins_by_email=resolved
    )


def gather_output_prefix_runs(
    bucket: str, *, profile: str | None, region: str | None
) -> dict[str, list[str]]:
    """Which run ids sit under each ``teams/<team>/runs/`` prefix in the outputs bucket.

    Listed with a delimiter rather than walked, so this reads the two levels of the layout
    it cares about and not the several hundred thousand objects beneath them.
    """
    teams = _list_common_prefixes(bucket, OUTPUTS_TEAM_PREFIX, profile=profile, region=region)
    found: dict[str, list[str]] = {}
    for team_prefix in teams:
        team = team_prefix.removeprefix(OUTPUTS_TEAM_PREFIX).rstrip("/")
        runs = _list_common_prefixes(
            bucket, f"{team_prefix}runs/", profile=profile, region=region
        )
        found[team] = sorted(prefix.rstrip("/").rsplit("/", 1)[-1] for prefix in runs)
    return found


def _list_common_prefixes(
    bucket: str, prefix: str, *, profile: str | None, region: str | None
) -> list[str]:
    completed = aws(
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            prefix,
            "--delimiter",
            "/",
            "--query",
            "CommonPrefixes[].Prefix",
        ],
        profile=profile,
        region=region,
    )
    if completed.returncode != 0:
        raise ReportInputError(f"could not list s3://{bucket}/{prefix}")
    answered = json.loads(completed.stdout or "null")
    return [str(entry) for entry in answered or []]


def gather_authored_path_segments(
    roots: Sequence[Path], *, logins_by_email: Mapping[str, str]
) -> dict[str, list[str]]:
    """Which directories each person has authored files under, in the clones given.

    A LOCAL CLONE RATHER THAN THE API, AND THAT IS NOT A SHORTCUT. Asking GitHub which paths
    a commit touched is one call per commit, and the repositories here carry tens of
    thousands. ``git log --name-only`` over every ref answers the same question in one
    process, and a clone is what a person running this already has.

    The identity in a commit is an author email and this report joins on GitHub logins, so
    the pair comes from :func:`sweep_commits`, which reads GitHub's own answer. An email
    that answer does not carry is skipped rather than guessed at.

    Path segments and never file names. ``src/olmo_core/data/tokenizer.py`` is a file called
    tokenizer.py that half the repository has edited, and counting it would place a dozen
    people in the tokenizer group on the strength of a module name.
    """
    segments: dict[str, set[str]] = {}
    for root in roots:
        name = root.resolve().name
        current: str | None = None
        for line in _git(root, "log", "--all", "--format=%x00%ae", "--name-only").splitlines():
            if line.startswith("\x00"):
                current = logins_by_email.get(line[1:].strip().casefold())
                continue
            if not line.strip() or current is None:
                continue
            for part in Path(line.strip()).parent.parts:
                segments.setdefault(current, set()).add(f"{name}:{part}")
    return {login: sorted(found) for login, found in sorted(segments.items())}


def gather(
    inventory: OrganizationInventory,
    *,
    organization: str,
    outputs_bucket: str,
    clones: Sequence[Path],
    profile: str | None,
    region: str | None,
) -> dict[str, Any]:
    """Ask the organization and the account for the facts this report cannot hold."""
    team_members = {
        team.github_team_slug: sorted(
            str(entry["login"])
            for entry in _github_listing(
                f"orgs/{organization}/teams/{team.github_team_slug}/members"
            )
        )
        for team in inventory.team_bindings.teams
    }
    swept = sweep_commits(organization)
    return {
        "observed_at": observed_now().isoformat(),
        "organization": organization,
        "github_team_members": team_members,
        "output_prefix_runs": gather_output_prefix_runs(
            outputs_bucket, profile=profile, region=region
        ),
        "repository_contributors": {
            name: sorted(logins) for name, logins in sorted(swept.contributors.items())
        },
        "repository_push_actors": {
            name: sorted(logins) for name, logins in sorted(swept.push_actors.items())
        },
        "authored_path_segments": gather_authored_path_segments(
            clones, logins_by_email=swept.logins_by_email
        ),
    }


def sync_lineage(
    bucket: str, destination: Path, *, profile: str | None, region: str | None
) -> None:
    """Pull the prefixes ``report_run_costs`` reads, through the AWS CLI it also uses.

    ``boto3`` is deliberately not a dependency of this project: it is in the Lambda runtime
    and nowhere else.
    """
    for prefix in LINEAGE_PREFIXES:
        completed = aws(
            ["s3", "sync", f"s3://{bucket}/{prefix}/", str(destination / prefix), "--quiet"],
            profile=profile,
            region=region,
        )
        if completed.returncode != 0:
            raise ReportInputError(f"could not read s3://{bucket}/{prefix}/")


def _checked_destination(path: Path) -> Path:
    """Refuse to gather straight into ``fixtures/``.

    This asks a live organization who has committed what and where their runs wrote, and
    that answer is somebody's to read before it is committed. Every capture tool in this
    repository refuses the same destination for the same reason.
    """
    if "fixtures" in path.resolve().parts:
        raise ReportInputError(
            f"{path} is under fixtures/. A gathered file names who has worked in every "
            "repository this organization holds, so it is local until somebody has read it "
            "and decided what belongs in a committed record."
        )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help="the gathered organization and bucket facts this report reads",
    )
    parser.add_argument(
        "--gather",
        action="store_true",
        help="ask GitHub and S3 through their CLIs and write --evidence first",
    )
    parser.add_argument(
        "--clone",
        type=Path,
        action="append",
        default=[],
        help=(
            "a local clone to read authorship from, repeatable. Its directory name has to "
            "be the repository name, because that is what the login lookup asks GitHub for."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--lineage-root", type=Path, help="a directory already holding records")
    source.add_argument(
        "--lineage-bucket",
        nargs="?",
        const=DEFAULT_LINEAGE_BUCKET,
        help="sync this bucket's intent/ and attempt/ prefixes first",
    )
    parser.add_argument("--outputs-bucket", default=DEFAULT_OUTPUTS_BUCKET)
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="a login to leave out of the draft, repeatable. For people who are not researchers.",
    )
    parser.add_argument("--csv", type=Path, help="write the draft rows here as well")
    parser.add_argument("--output", type=Path, help="write the report here rather than to stdout")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)

    with tempfile.TemporaryDirectory() as scratch:
        try:
            inventory = load_yaml(options.config_dir / "organization.yaml", OrganizationInventory)
            if options.gather:
                # Checked before anything is asked, so a destination that would be refused
                # costs no calls and leaves nothing half written.
                destination = _checked_destination(options.evidence)
                destination.write_text(
                    json.dumps(
                        gather(
                            inventory,
                            organization=options.organization,
                            outputs_bucket=options.outputs_bucket,
                            clones=options.clone,
                            profile=options.profile,
                            region=options.region,
                        ),
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            evidence = read_evidence(options.evidence)
            if options.lineage_bucket:
                root = Path(scratch)
                sync_lineage(
                    options.lineage_bucket,
                    root,
                    profile=options.profile,
                    region=options.region,
                )
            else:
                root = options.lineage_root
            intents, _attempts, unparsed = read_records(root)
        except (ReportInputError, CaptureFailedError, OSError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return EXIT_UNUSABLE

    drafted = assignments(
        inventory,
        runs=submitted_runs(intents),
        evidence=evidence,
        exclude=options.exclude,
    )

    if options.csv is not None:
        options.csv.write_text(render_csv(drafted), encoding="utf-8")

    report = render(drafted, evidence, unparsed=unparsed)
    if options.output is not None:
        options.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
