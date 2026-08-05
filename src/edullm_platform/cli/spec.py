"""``.edullm/run.yaml``: the half of a submission that is a property of the code.

WHAT IS IN HERE AND WHAT IS NOT IS THE WHOLE DESIGN, and it is
``docs-frank/reference/system-overview.md``'s under "The submission path": the spec holds
the command, the workload profile, the fan-out parameter and a suggested shape, because
those are facts about the repository at that commit and travel with it in version control.
Everything else is supplied at submit time -- the compute profile, which spans a
hundredfold in price and is what the approver is pricing; the inputs, which are the
science; the experiment, which is how a run groups.

**Neither the team nor the machine is settled here, and they are settled here differently.**
The team is absent outright: one commit run by two people belongs to two teams, so a team
in a version-controlled file would be wrong for the second person and nothing would notice.
The machine is present as ``suggested_compute`` and is a default rather than a decision --
the value always travels to the form explicitly, ``--compute`` overrides it, and ``check``
prices whichever one wins. The overview says both things in the same paragraph and they are
not in tension: the suggestion is what the code's author thinks it needs, and the
submission is what somebody is paying for today.

**The command is one string and not a list, which is the form's own shape.** The compile
job's ``Assemble the submission form`` step POSIX-splits the text box, so a spec holding a
pre-split list would be a second parse of the same text, disagreeing with the workflow's
the first time somebody wrote a quote. It is split here by ``shlex`` exactly as the
workflow splits it.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

__all__ = [
    "SPEC_DIRECTORY",
    "SPEC_PATH",
    "RunSpec",
    "SpecFanOut",
    "SpecModel",
    "SpecUnreadableError",
    "find_spec",
    "load_spec",
    "render_spec",
]

#: Where a research repository keeps everything this platform reads out of it -- the
#: Dockerfile the build workflow builds, and this. One directory rather than a dotfile per
#: concern, which is the layout ``config/repositories.yaml`` already assumes for the build.
SPEC_DIRECTORY = ".edullm"
SPEC_PATH = f"{SPEC_DIRECTORY}/run.yaml"


class SpecUnreadableError(ValueError):
    """The spec is not a document this platform can read.

    Distinct from a refusal: a spec that will not parse has not described a submission that
    was declined, and the two send a reader to different halves of the file.
    """


def _text(value: object) -> object:
    """Accept the block scalars a hand-written spec uses, as one line of command text.

    ``command: >-`` folds its newlines to spaces, which is what makes a long command
    readable in the file, and PyYAML has already done that by the time this runs. What is
    left is the trailing and leading whitespace a folded scalar can still carry, and a
    command whose first word is empty is refused by the contract layer for a reason nobody
    reading their own file would guess at.
    """
    return value.strip() if isinstance(value, str) else value


class SpecModel(BaseModel):
    """As strict as a contract and deliberately not one of them.

    ``ContractModel`` would be the obvious base and is the wrong one, because
    ``proof_bundle.discover_contract_models`` records every subclass of it in four committed
    proof bundles as a published claim about this repository's contracts. Those are the
    models whose structural digest matters: payloads are written against them and stored
    immutably, so a field added to one may refuse a record already in the lineage store.
    A spec is a file in somebody else's repository that this binary reads and rewrites; no
    stored payload is written against it and no digest is taken over it, so recording it
    would put a CLI convenience in a table that means "changing this invalidates history".

    What is kept is the strictness, because the reason for it is the same. ``extra="forbid"``
    is what turns a mistyped key in a hand-written file into a refusal naming the key rather
    than a field silently reading as its default.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class SpecFanOut(SpecModel):
    """The fan-out, in the spec because a sweep is a property of the code that loops.

    Two fields and not three. ``fanout_parallelism`` was removed from the submission form
    because Batch's ``SubmitJob`` takes an array size and no concurrency cap, so the number
    was recorded and never applied; see ``FanOut`` in ``contracts/manifest.py``. A spec
    field here would put it back under a new name.
    """

    size: int = Field(ge=2)
    index_parameter: str = Field(min_length=1)


class RunSpec(SpecModel):
    schema_version: Literal[1]
    workload_profile: str = Field(min_length=1)
    #: A default for ``--compute`` and never a decision. Optional because a repository whose
    #: workloads all run on a CPU has nothing useful to suggest, and a suggestion invented to
    #: fill a required field is the ``compute_profile`` fiction ``WorkloadProfile`` removed.
    suggested_compute: str | None = Field(default=None, min_length=1)
    command: Annotated[str, BeforeValidator(_text)] = Field(min_length=1)
    fanout: SpecFanOut | None = None

    @model_validator(mode="after")
    def validate_command_splits(self) -> Self:
        if not shlex.split(self.command):
            raise ValueError(
                "command must name a program; this one splits into no words at all"
            )
        return self

    @property
    def argv(self) -> tuple[str, ...]:
        """The command as the compile job will split it, which is POSIX word splitting."""
        return tuple(shlex.split(self.command))


def find_spec(start: Path) -> Path | None:
    """The spec governing this directory, or ``None`` if the tree carries none.

    Walked upward so that ``edullm check`` works from a subdirectory of the repository,
    which is where people stand. It stops at the first hit rather than at the repository
    root, because a nested spec is somebody deliberately overriding the one above it.
    """
    here = start.resolve()
    for directory in (here, *here.parents):
        candidate = directory / SPEC_PATH
        if candidate.is_file():
            return candidate
    return None


def load_spec(path: Path) -> RunSpec:
    """The spec at ``path``, or a sentence about why it is not one.

    WHAT PYDANTIC PRINTS IS NOT WHAT SOMEBODY EDITING A YAML FILE NEEDS. Its own rendering
    of one bad field is five lines, a repeated model name, the offending value echoed as a
    Python repr and a link to ``errors.pydantic.dev`` -- and a researcher who mistyped
    ``schema_version`` meets all of it on their first ``check``, which teaches them the
    tool broke rather than that their file has a typo in it. Every field it objected to
    gets one line naming the field, because they are about to open the file and the second
    problem is worth knowing before they close it.
    """
    import yaml
    from pydantic import ValidationError

    from edullm_platform.cli.preflight import validation_messages
    from edullm_platform.config import SafeUniqueKeyLoader

    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=SafeUniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise SpecUnreadableError(f"{path} is not readable as YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise SpecUnreadableError(
            f"{path} must be a mapping of fields, and it is a "
            f"{type(document).__name__} instead"
        )
    try:
        return RunSpec.model_validate(document)
    except ValidationError as exc:
        said = "\n".join(f"  {message}" for message in validation_messages(exc))
        raise SpecUnreadableError(
            f"{path} is not a run spec this platform can read:\n{said}"
        ) from exc


def render_spec(spec: RunSpec, *, notes: tuple[str, ...] = ()) -> str:
    """The spec as a file a person will edit, rather than as YAML a dumper produced.

    Written by hand rather than through ``yaml.safe_dump`` for one reason that matters: the
    scaffold's whole job is to be edited, and a dumper cannot emit the comments that say
    which lines were guessed. The field order is the reading order of the design -- what
    kind of run, on what, running what -- rather than alphabetical.
    """
    lines = [*notes] if notes else []
    lines.extend(
        [
            f"schema_version: {spec.schema_version}",
            f"workload_profile: {spec.workload_profile}",
        ]
    )
    if spec.suggested_compute is not None:
        lines.append(f"suggested_compute: {spec.suggested_compute}")
    lines.append("command: >-")
    lines.extend(f"  {segment}" for segment in _fold(spec.command))
    if spec.fanout is not None:
        lines.extend(
            [
                "fanout:",
                f"  size: {spec.fanout.size}",
                f"  index_parameter: {spec.fanout.index_parameter}",
            ]
        )
    return "\n".join(lines) + "\n"


def _fold(command: str, width: int = 78) -> list[str]:
    """Break a command across lines at word boundaries, the way a folded scalar reads back.

    Folding is safe here and would not be safe in general: a folded block scalar rejoins its
    lines with single spaces, so breaking only where a space already is round-trips to the
    same string. A break inside a quoted word would not, which is why this never splits one.
    """
    folded: list[str] = []
    current = ""
    for word in command.split(" "):
        candidate = word if not current else f"{current} {word}"
        if len(candidate) > width and current:
            folded.append(current)
            current = word
        else:
            current = candidate
    if current:
        folded.append(current)
    return folded or [command]
