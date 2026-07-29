"""Which teams each workload role can actually reach, read out of the committed templates.

Phase 5's central claim is that two teams using the same platform stay isolated in storage.
This is the half of that claim which can be established without deploying anything: what the
grants *say*, read from the templates rather than remembered.

**The measurement it exists to make visible, taken 2026-07-29.** The two workload roles are
not scoped the same way and nothing said so:

============================================== =============================== ==============
Role                                           S3 object scope                 Teams reachable
============================================== =============================== ==============
``…-batch-workload`` (CPU)                     ``…/teams/*/runs/*``            **every team**
``…-batch-gpu-workload``                       ``…/teams/platform/runs/*``     one
============================================== =============================== ==============

So the CPU path has no cross-team isolation at all. Today that costs nothing, because one
team exists and the wildcard has exactly one thing to match. The moment a second team is
bound it becomes the difference between a platform that isolates teams and one that says it
does -- and it would arrive silently, because no run fails and no test notices a wildcard
matching a name it did not previously match.

**Read from the template, and that is a real limit.** A role widened in a console leaves
every citation here green. What this establishes is what the account will be *asked* for,
which is where a cross-team grant is introduced; whether the account still holds it is what
the role-drift capture is for, and the two are different questions.

**Why the wildcard is not simply narrowed here.** It is a deployed grant, and narrowing it
is a laptop-applied IAM change that drifts a committed Phase 3 capture. Phase 4 met the same
problem and answered it by giving the GPU path its own role trio rather than tightening the
CPU one -- a phase should not invalidate the previous phase's evidence to close its own
check. The same answer applies again, and it is Phase 5's to make rather than a tidy-up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from edullm_platform.contracts.results import OUTPUTS_BUCKET
from edullm_platform.role_drift import TemplateRole, load_template_roles

__all__ = [
    "EVERY_TEAM",
    "WORKLOAD_ROLE_TEMPLATES",
    "TeamReach",
    "reach_of",
    "teams_reachable_by",
    "workload_roles",
]

#: What a ``*`` in the team segment means, spelled rather than left as punctuation. A set
#: containing this is not a set of one team; it is a set containing every team there will
#: ever be, and code comparing it against a roster has to say which it meant.
EVERY_TEAM: Final = "*"

#: Every role a container runs as, and the template that declares it. The execution and
#: instance roles are deliberately absent: neither is what the workload's own code runs as,
#: so neither is what a cross-team check is about.
WORKLOAD_ROLE_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    ("sbsandbox-intern-edullm-batch-workload", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-batch-gpu-workload", "infra/iam/batch-gpu-roles.yaml"),
)

#: The object ARNs and prefix conditions this reads, in the one shape the platform writes.
#: Anchored on the outputs bucket by name: a grant on a different bucket is a different
#: question, and quietly folding one in here would report isolation about the wrong store.
_OBJECT_ARN = re.compile(
    rf"^arn:\$\{{AWS::Partition\}}:s3:::{re.escape(OUTPUTS_BUCKET)}/teams/(?P<team>[^/]+)/runs/"
)
_PREFIX_CONDITION = re.compile(r"^teams/(?P<team>[^/]+)/runs/")


@dataclass(frozen=True)
class TeamReach:
    """One team segment a role can reach, and what it may do there.

    The actions are carried rather than collapsed to a boolean because read and write fail
    differently. A role that can read another team's outputs leaks research; one that can
    write there corrupts it; one that can only list learns what exists. A check that wanted
    to distinguish those cannot, once they are one flag.
    """

    team: str
    actions: frozenset[str]

    @property
    def is_every_team(self) -> bool:
        return self.team == EVERY_TEAM


def _teams_in(resource: str) -> str | None:
    matched = _OBJECT_ARN.match(resource)
    return matched.group("team") if matched else None


def reach_of(role: TemplateRole) -> tuple[TeamReach, ...]:
    """Which team segments this role's Allow statements reach, and with what.

    Both spellings are read, because they are two different grants that both scope by team
    and a check that read one would report the other as absent. An object action is scoped
    by the resource ARN; ``s3:ListBucket`` is a bucket-level action that cannot be, and is
    scoped instead by an ``s3:prefix`` condition on the bucket ARN. A role granted
    ``ListBucket`` with no condition can enumerate every team's output while its object
    grants look perfectly narrow.
    """
    found: dict[str, set[str]] = {}
    for policy in role.inline_policies:
        for statement in policy.statements:
            if statement.effect != "Allow":
                continue
            actions = set(statement.action_match.actions)
            teams = {
                team
                for resource in statement.resource_match.resources
                if (team := _teams_in(resource)) is not None
            }
            for condition in statement.conditions:
                if condition.condition_key != "s3:prefix":
                    continue
                for value in condition.values:
                    matched = _PREFIX_CONDITION.match(value)
                    if matched is not None:
                        teams.add(matched.group("team"))
            for team in teams:
                found.setdefault(team, set()).update(actions)
    return tuple(
        TeamReach(team=team, actions=frozenset(actions))
        for team, actions in sorted(found.items())
    )


def workload_roles(repo_root: Path) -> tuple[TemplateRole, ...]:
    """Every workload role, in registry order, refused if one is not where it is declared."""
    found: list[TemplateRole] = []
    for role_name, relative_path in WORKLOAD_ROLE_TEMPLATES:
        declared = [
            role
            for role in load_template_roles(repo_root / relative_path)
            if role.role_name == role_name
        ]
        if len(declared) != 1:
            raise ValueError(
                f"{relative_path} must declare exactly one role named {role_name}; "
                f"found {len(declared)}"
            )
        found.append(declared[0])
    return tuple(found)


def teams_reachable_by(role: TemplateRole) -> frozenset[str]:
    """The team segments this role can reach, with the wildcard left as itself.

    Not expanded against a roster, deliberately. Expanding it would answer "which teams that
    exist today can this reach", and the interesting question is the other one: whether the
    grant is written in terms of teams at all, or in terms of a pattern that will match the
    next team without anybody deciding it should.
    """
    return frozenset(entry.team for entry in reach_of(role))
