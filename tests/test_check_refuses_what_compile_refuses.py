"""Every refusal the compile job can make, held against every refusal a laptop makes.

**THIS IS THE TEST FOR A CLASS OF DEFECT RATHER THAN FOR A DEFECT.** ``edullm check`` exists
so that a submission is refused before it costs a dispatch, a queue wait and somebody's
approval. That promise is worth exactly as much as the overlap between the two lists, and
nothing was comparing them. Twice now the lists have parted and both times it was found by
reading rather than by anything going red:

* ``require_a_tensor_parallel_flag_vllm_reads`` was called by ``compile_submission`` and by
  nothing in ``cli/preflight.py``, so a sweep spelling vLLM's tensor-parallel option the way
  the harness accepts and silently discards cleared ``check`` and was refused after dispatch.
* The quoting rule before it, on the same shape and at the same cost.

``cli/preflight.py`` and ``edullm_platform/errors.py`` both carry paragraphs saying a second
spelling of a rule is a second answer to a settled question. ``tests/test_refusal_codes.py``
holds the *vocabulary* to one set of words. This holds the *population* to one set of rules,
which is the half that was still open: two sides can agree perfectly about what
``process_per_device`` means while only one of them asks.

**NEITHER LIST IS WRITTEN DOWN HERE, WHICH IS THE WHOLE OF WHY THIS IS WORTH HAVING.** A test
naming the compile-time refusals that exist today is green on the next one, and the next one
is the one somebody will add without a local counterpart. So both sides are walked out of the
source: every function reachable from the entry point, every ``raise`` in it, and the
``reason_code`` of whatever class it names. A rule added to ``compile_submission`` joins the
compile list by being written, and fails here until it joins the other one too.

**WHAT A DIFFERENCE IS ALLOWED TO BE.** These, and each one is checked rather than asserted
in prose:

``preflight.DEFERRED_TO_SUBMIT``
    A question a laptop cannot answer, declared to the reader as deferred rather than passed.
    ``no_published_image`` is the case this was built for: asking the registry needs a
    credential this binary does not hold and must not.

:data:`ANSWERED_UNDER_OTHER_CODES`
    A question both sides put to the same function, whose answer the local side reports under
    codes of its own. The shared call is named and this asserts both sides reach it, so the
    entry cannot rot into a claim about a check nobody makes.

:data:`UNREACHABLE_FROM_THIS_BINARY`
    A refusal that needs a form field ``SubmissionRequest`` does not carry, so no submission
    this binary builds can reach it. The absent field is named and this asserts it is still
    absent, so adding the field turns the entry red rather than leaving it a lie.

Anything else is the defect, and the assertion prints it with the code that names it.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Final, get_args

import tools.compile_submission
from edullm_platform.cli import preflight
from edullm_platform.contracts.policy import DeniedOutrightCondition
from tests.test_refusal_codes import dotted_name, package_modules, resolve

#: One function, as the module that defines it and the name it is defined under.
Function = tuple[str, str]

#: Where the credential-free compile job starts. ``tools/compile_submission.py`` rather than
#: ``submission.compile_submission``, because three of the refusals a submitter meets are
#: raised beside that call rather than inside it -- the roster, the repository and a retired
#: dataset release -- and a walk from the inner function would report all three as compile
#: refusals nobody makes.
COMPILE_ENTRY: Final[Function] = ("tools.compile_submission", "main")

#: Where every refusal ``edullm check`` prints comes from. ``cli/main.py``'s own merge step
#: rather than ``run_preflight``, for the mirror of the reason above: the working-tree
#: refusals and the unresolved-team refusal are collected around that call rather than in it.
#: Private, and named anyway, because it is the function both ``check`` and ``submit`` go
#: through and a public alias for it would be a second answer to which one that is.
LOCAL_ENTRY: Final[Function] = ("edullm_platform.cli.main", "_preflight")


@dataclass(frozen=True)
class AnsweredUnderOtherCodes:
    """A compile-time refusal whose question the local side asks through the same function.

    Nothing clears ``check`` and meets one of these, because the same call refuses the same
    submission on both sides. What differs is the word on the refusal, and both differences
    below are deliberate and recorded where the exception is defined.
    """

    code: str
    #: The function both sides call. Asserted reachable from both entry points, so an entry
    #: here cannot outlive the shared call it rests on.
    shared_call: Function
    why: str


@dataclass(frozen=True)
class UnreachableFromThisBinary:
    """A compile-time refusal no submission this binary can build is able to trip."""

    code: str
    #: The field on ``preflight.SubmissionRequest`` whose absence is the reason. Asserted
    #: absent, so adding it goes red here rather than silently reopening the gap.
    absent_field: str
    why: str


ANSWERED_UNDER_OTHER_CODES: Final = (
    AnsweredUnderOtherCodes(
        code="denied_outright_by_policy",
        shared_call=("edullm_platform.admission", "denied_outright_conditions"),
        why=(
            "The compile step raises one refusal saying at least one condition was tripped. "
            "run_preflight calls the same function and puts each tripped condition on a "
            "refusal of its own, which is finer rather than weaker: a submitter reads which "
            "condition, and errors.py records the split."
        ),
    ),
    AnsweredUnderOtherCodes(
        code="team_not_a_slug",
        shared_call=("edullm_platform.manifest_helpers", "build_request_facts"),
        why=(
            "RequestFacts refuses a team that is not a slug on both sides, out of the same "
            "call. The compile step translates that one pydantic error into a refusal naming "
            "the field; the laptop reports it as submission_cannot_be_priced, and reaches "
            "unregistered_team first because a team of that shape is in no roster."
        ),
    ),
)

UNREACHABLE_FROM_THIS_BINARY: Final = (
    UnreachableFromThisBinary(
        code="image_not_published_from_the_commit",
        absent_field="image_digest",
        why=(
            "Only resolve_image's override branch raises it, and the override is the form's "
            "image_digest field. submit leaves that field off entirely -- which is what makes "
            "the workflow derive the image from the commit -- so this refusal is reachable "
            "from the Actions form and not from here. Deferring it would name a condition a "
            "reader of check cannot meet."
        ),
    ),
)


def refusal_code_of(named: object) -> str:
    """The code a raised class is known by, or the empty string for anything else.

    Any exception carrying a non-empty ``reason_code``, rather than the
    ``SubmissionRefusedError`` family alone. ``ComputeProfileResolutionError`` is the other
    family and is the reason: ``UnpriceableComputeProfileError`` borrows its code so that an
    unpriceable profile is one word on both sides, and a walk that knew only about refusals
    would report the compile step raising a code with nothing local behind it while
    ``_find_compute`` was answering it all along.
    """
    if not isinstance(named, type) or not issubclass(named, Exception):
        return ""
    code = getattr(named, "reason_code", "")
    return code if isinstance(code, str) else ""


def _locally_imported(definition: ast.AST) -> dict[str, ModuleType]:
    """The modules a function imports in its own body, by the name it binds them under.

    **A RULE BEHIND A FUNCTION-LOCAL IMPORT IS STILL A RULE, AND THE FIRST DRAFT OF THIS WALK
    COULD NOT SEE ONE.** ``compile_submission`` imports ``denied_outright_conditions`` inside
    itself on purpose -- admission owns that rule, and importing it at module scope would make
    the compile step the authority on what is denied -- so the name is bound nowhere the
    module's namespace can be asked about it.
    """
    found: dict[str, ModuleType] = {}
    for node in ast.walk(definition):
        if not isinstance(node, ast.ImportFrom) or node.module is None or node.level:
            continue
        try:
            imported = import_module(node.module)
        except ImportError:  # pragma: no cover -- an import this tree cannot satisfy
            continue
        for alias in node.names:
            found[alias.asname or alias.name] = imported
    return found


def _call_graph() -> tuple[dict[Function, set[str]], dict[Function, set[Function]]]:
    """What each function raises and what each function calls, read out of the source.

    Both maps are keyed on the same pair, and a call is recorded only where the name resolves
    to something this distribution defines. Resolution is against the imported module's own
    namespace rather than against its import statements, so an alias and a re-export both land
    on the function they name -- the argument ``tests/test_refusal_codes.py`` makes at length
    about the same walk -- and falls back to whatever the function imported for itself.

    Nested definitions are attributed to the function they sit in as well as to themselves.
    That over-reports in the direction that cannot hide a rule, which is the only direction a
    reachability walk is allowed to be wrong in here.
    """
    raises: dict[Function, set[str]] = defaultdict(set)
    calls: dict[Function, set[Function]] = defaultdict(set)

    for module, tree in _walked_modules():
        for definition in ast.walk(tree):
            if not isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            here: Function = (module.__name__, definition.name)
            scopes = (module, *_locally_imported(definition).values())

            def named_by(node: ast.expr, scopes: tuple[object, ...] = scopes) -> object:
                parts = dotted_name(node)
                if parts is None:
                    return None
                for scope in scopes:
                    found = resolve(scope, parts)
                    if found is not None:
                        return found
                    if len(parts) > 1:
                        # A locally imported name is bound bare, so `admission.foo` resolves
                        # against the module and `foo` against what the function imported.
                        found = resolve(scope, parts[-1:])
                        if found is not None:
                            return found
                return None

            for node in ast.walk(definition):
                if isinstance(node, ast.Raise) and node.exc is not None:
                    thrown = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                    code = refusal_code_of(named_by(thrown))
                    if code:
                        raises[here].add(code)
                if isinstance(node, ast.Call):
                    called = named_by(node.func)
                    origin = getattr(called, "__module__", None) or ""
                    if callable(called) and origin.startswith("edullm_platform"):
                        calls[here].add((origin, called.__name__))
    return raises, calls


def _walked_modules() -> Iterator[tuple[ModuleType, ast.Module]]:
    """Every module of the distribution, and the one tool that drives the compile job."""
    yield from package_modules()
    source = Path(str(tools.compile_submission.__file__))
    yield tools.compile_submission, ast.parse(source.read_text(encoding="utf-8"))


RAISES, CALLS = _call_graph()


def _reached(entry: Function) -> set[Function]:
    """Every function reachable from one entry point, the entry point included."""
    found: set[Function] = set()
    pending = [entry]
    while pending:
        here = pending.pop()
        if here in found:
            continue
        found.add(here)
        pending.extend(CALLS.get(here, ()))
    return found


def refusal_codes_from(entry: Function) -> frozenset[str]:
    """Every refusal code a call of this function can end at."""
    return frozenset(code for here in _reached(entry) for code in RAISES.get(here, ()))


def codes_named_in_preflight() -> frozenset[str]:
    """The codes ``cli/preflight.py`` reads off a class by naming it.

    The other half of how the local side covers a rule. Five of the refusals it makes are
    questions it asks over again rather than exceptions it catches -- there is no catalog
    lookup on a laptop to catch an ``UnregisteredWorkloadProfileError`` out of -- so the code
    comes from ``SomeError.reason_code`` and the walk above cannot see it.
    """
    tree = ast.parse(Path(str(preflight.__file__)).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "reason_code":
            continue
        parts = dotted_name(node.value)
        code = refusal_code_of(None if parts is None else resolve(preflight, parts))
        if code:
            found.add(code)
    return frozenset(found)


def deferred_codes() -> frozenset[str]:
    return frozenset(code for code, _ in preflight.DEFERRED_TO_SUBMIT)


def test_every_compile_time_refusal_is_asked_here_or_accounted_for() -> None:
    """Mutation: delete the ``require_a_tensor_parallel_flag_vllm_reads`` call from
    ``_check_command``, or add any new ``raise`` of a coded refusal to ``compile_submission``.

    Either one puts a code on the compile side with nothing on this side, and neither needs
    anybody to have remembered to extend a list here. That is the property: the day a rule is
    added to the compile step and forgotten on the laptop, this goes red naming the code.
    """
    compiled = refusal_codes_from(COMPILE_ENTRY)
    locally = refusal_codes_from(LOCAL_ENTRY) | codes_named_in_preflight()

    assert compiled, "no compile-time refusal was found at all, which is a broken walk"
    accounted = (
        deferred_codes()
        | {entry.code for entry in ANSWERED_UNDER_OTHER_CODES}
        | {entry.code for entry in UNREACHABLE_FROM_THIS_BINARY}
    )
    unasked = sorted(compiled - locally - accounted)

    assert not unasked, (
        "the compile step refuses " + ", ".join(unasked) + " and edullm check does not, so a "
        "submitter is told their submission is good and refused after the dispatch. Ask the "
        "same question in cli/preflight.py, or -- if a laptop genuinely cannot -- say so in "
        "preflight.DEFERRED_TO_SUBMIT so the output reports it as deferred rather than passed."
    )


def test_nothing_is_excused_that_the_compile_step_no_longer_refuses() -> None:
    """Mutation: leave an entry behind after closing the gap it excused.

    The tables above and ``DEFERRED_TO_SUBMIT`` are read by a person deciding whether a clean
    ``check`` means anything, and an entry naming a refusal that no longer exists costs that
    reader more than it saves. Held both ways so the ledger cannot outlive its subject.
    """
    compiled = refusal_codes_from(COMPILE_ENTRY)
    excused = {entry.code for entry in ANSWERED_UNDER_OTHER_CODES} | {
        entry.code for entry in UNREACHABLE_FROM_THIS_BINARY
    }
    stranded = sorted(excused - compiled)

    assert not stranded, (
        f"{', '.join(stranded)} is excused here and the compile step does not refuse it. "
        "Delete the entry."
    )


def test_every_deferred_check_names_something_that_can_actually_refuse_a_run() -> None:
    """Mutation: defer a check nothing makes, on either side.

    ``DEFERRED_TO_SUBMIT`` is the honest half of this verb -- two questions reported as not
    asked rather than as passed -- and it is only honest while every code in it is a refusal
    somebody downstream really makes. A deferral naming nothing reads as diligence and is
    noise in front of a researcher who is trying to submit a run.
    """
    downstream = refusal_codes_from(COMPILE_ENTRY) | set(get_args(DeniedOutrightCondition))
    invented = sorted(deferred_codes() - downstream)

    assert not invented, (
        f"preflight.DEFERRED_TO_SUBMIT defers {', '.join(invented)}, which neither the "
        "compile step raises nor config/policy.yaml denies outright."
    )


def test_a_refusal_answered_under_other_codes_rests_on_a_call_both_sides_make() -> None:
    """Mutation: excuse a code by writing an entry, without either side asking anything.

    This is what stops :data:`ANSWERED_UNDER_OTHER_CODES` becoming the place a gap goes to be
    forgotten. The claim each entry makes is that one function refuses this submission on both
    paths, and that is a fact about the call graph rather than about the prose beside it.
    """
    compiled, locally = _reached(COMPILE_ENTRY), _reached(LOCAL_ENTRY)

    for entry in ANSWERED_UNDER_OTHER_CODES:
        module, name = entry.shared_call
        assert entry.shared_call in compiled, (
            f"{module}.{name} is named as what covers {entry.code} and the compile step does "
            "not call it"
        )
        assert entry.shared_call in locally, (
            f"{module}.{name} is named as what covers {entry.code} and edullm check does not "
            f"call it, so nothing local asks the question. {entry.why}"
        )


def test_a_refusal_called_unreachable_needs_a_field_the_request_still_lacks() -> None:
    """Mutation: put the missing field on ``SubmissionRequest`` and leave the entry.

    An entry here says a refusal cannot be reached because this binary cannot fill the form
    field that trips it. Adding the field makes that false, and makes the refusal live: it
    would then clear ``check`` and arrive from the compile job.
    """
    for entry in UNREACHABLE_FROM_THIS_BINARY:
        assert entry.absent_field not in preflight.SubmissionRequest.__dataclass_fields__, (
            f"SubmissionRequest now carries {entry.absent_field}, so {entry.code} is reachable "
            f"from a submission this binary builds and is no longer excused. {entry.why}"
        )
