"""What a phase acceptance gate is, for every phase that is only its criteria.

Phases 1 to 4 each execute a table of criteria and report what it found, and until this
module existed each of them said so in a file of its own: four report models differing in
one count, four notes derived the same way, and four command-line entry points that were
byte-identical once ``Phase N`` was normalised. Answering "how does a phase gate work"
meant reading four files and diffing them to discover the answer was the same one four
times.

It is one answer now. A phase module states its identity, the number of criteria its
definition records, and the function that produces them; everything else a reader needs
about what a gate computes and what its command prints is here.

**Phase 0 is not one of these and is deliberately not generalised over.** It carries a
second group of operational inventory checks from an earlier definition of that phase, so
its report has two groups, two notes and a verdict that ANDs them, and its command prints
no pilot line. A base with a group that four of five phases do not have would be the kind
of near-fit this module exists to stop being written five times.

The exit code is the gate's and only the gate's. A pilot-ready phase with a red gate still
exits 1, because the gate is what the exit code has always meant and reusing it for
adoption would silently change every caller's question.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, ClassVar, Self

import yaml
from pydantic import (
    BeforeValidator,
    Field,
    ValidationError,
    computed_field,
    model_validator,
)

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.criteria import (
    CriteriaDefinitionError,
    CriterionResult,
    PilotVerdict,
    pilot_verdict,
)
from edullm_platform.criteria_runner import NestedExecutionError
from edullm_platform.status_prose import checked_phase_criteria_note, gate_and_pilot_line

__all__ = ["OrderedCriteria", "PhaseGateReport", "run_gate_command"]

#: How every phase's criteria arrive. The count each phase records is a constraint of its
#: own and stays on its own field; what is shared is the refusal of anything but a list or
#: a tuple, so that a report cannot be built from a set and silently lose the order the
#: phase numbers its criteria in.
OrderedCriteria = Annotated[
    tuple[CriterionResult, ...], BeforeValidator(require_ordered_sequence)
]


class PhaseGateReport(ContractModel):
    """One phase's gate: the criteria it executed, and the two verdicts they support.

    A subclass states :attr:`phase` and re-declares ``phase_criteria`` with the number of
    criteria its definition records. That count is the only reason the field is declared
    twice: it is part of the report's published schema, so a shared minimum would either
    be no constraint at all or the wrong one for three phases out of four.

    A subclass that needs a field of its own is describing a phase this base is the wrong
    shape for, and should say so by not inheriting from it.
    """

    #: The phase this report is about, spelled as a reader sees it: ``Phase 3``. A class
    #: variable rather than a field, because it is a property of the report type, and a
    #: field a caller could supply is a field that can name a phase the criteria are not
    #: from.
    phase: ClassVar[str]

    phase_criteria: OrderedCriteria = Field(strict=False)
    phase_criteria_note: str = ""

    @model_validator(mode="after")
    def _derive_and_check_the_note(self) -> Self:
        # Derived rather than defaulted, so a caller cannot supply one and the field
        # cannot hold a sentence nothing computed. The model is frozen, which is why this
        # is written through object.__setattr__ at the end of validation.
        object.__setattr__(
            self,
            "phase_criteria_note",
            checked_phase_criteria_note(self.phase_criteria, phase=self.phase),
        )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return all(criterion.passed for criterion in self.phase_criteria)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pilot(self) -> PilotVerdict:
        """The adoption verdict, computed beside the gate's and never folded into it.

        Derived rather than stored, for the reason the note is: a field a caller could
        supply is a field that can disagree with the criteria printed beside it.
        """
        return pilot_verdict(self.phase_criteria)


def run_gate_command(*, phase: str, evaluate: Callable[[Path], PhaseGateReport]) -> int:
    """Run one phase's gate against the working directory and report both verdicts.

    ``evaluate`` is taken as an argument rather than imported here, so that a phase's
    criteria are named in the phase's own module and nowhere else, and so that each
    command stays a module whose evaluation a test can replace without starting a gate.
    """
    repo_root = Path.cwd()
    try:
        result = evaluate(repo_root)
    except NestedExecutionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CriteriaDefinitionError as exc:
        print(f"the {phase} criteria definition is not usable: {exc}", file=sys.stderr)
        return 2
    # The named failures are what reading a repository has actually raised; the catch-all
    # after them is why an unanticipated one is still an exit 2 rather than a traceback.
    except (OSError, json.JSONDecodeError, yaml.YAMLError, TypeError, ValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI must map unexpected failures to exit 2
        print(str(exc), file=sys.stderr)
        return 2

    sys.stdout.write(canonical_json_bytes(result).decode("utf-8") + "\n")
    # Both verdicts, on stderr so that stdout stays exactly the canonical report a caller
    # parses. The exit code below is the gate's and only the gate's: a pilot-ready phase
    # with a red gate still exits 1, because the gate is what the exit code has always
    # meant and reusing it for adoption would silently change every caller's question.
    print(
        gate_and_pilot_line(phase=phase, gate_passed=result.passed, verdict=result.pilot),
        file=sys.stderr,
    )
    return 0 if result.passed else 1
