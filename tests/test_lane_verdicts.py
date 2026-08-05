"""The ruling that the lane is ungated, and the wire that fails when a gate reaches it.

WHY THIS FILE IS SEPARATE FROM tests/test_lane_preflight.py. That one says what the lane does
refuse and is a test of behaviour. This one says what it may never refuse and is a test of a
design decision, which is a different thing with a different failure mode: the way an ungated
route stops being one is not a bad refusal, it is a reasonable-looking call to a function that
brings twelve of them along. Every gate that has ever leaked into a route like this arrived that
way, one defensible line at a time.

THE OWNER'S POSITION, WHICH THIS FILE IS THE ENFORCEMENT OF. A gate in the lane is a defect
rather than a trade-off. A researcher who explores somewhere else will submit from somewhere
else, so the lane's job is to be the easiest place to start, and every check it makes is a
reason to use a personal AWS account instead. The recorded path earns its refusals because a
record is being made; nothing here is recorded.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN = PROJECT_ROOT / "src" / "edullm_platform" / "cli" / "main.py"

#: The verbs the ruling covers, and the functions in ``main.py`` that implement them.
LANE_VERB_FUNCTIONS: Final = ("_run", "_shell", "_lane_session", "_start_a_machine")

#: **THE RULING, AS A LIST OF NAMES.** Everything the submission path refuses that the lane must
#: not, each with the reason it is a permission rather than a spelling. Adding to this list is
#: cheap and removing from it is the deliberate act, which is the way round it has to be.
LANE_VERDICTS: Final = {
    "run_preflight": (
        "the whole submission preflight, which refuses an unregistered repository, an "
        "uncommitted tree, an unpushed commit, an off-roster submitter and an unpriced "
        "workload. Every one of those protects a record, and no record is made here."
    ),
    "working_tree_refusals": (
        "uncommitted_changes and commit_not_pushed. Exploring is the thing somebody does with "
        "uncommitted changes, and what runs on a lane machine is the bytes on the laptop rather "
        "than a commit anybody could fetch."
    ),
    "resolve_compute_profile_for_execution": (
        "unprovisioned_compute, which is a statement about a Batch queue existing. A lane "
        "machine is not a Batch job, so a priced shape with no queue is one a researcher may "
        "legitimately start."
    ),
    "read_run_facts": (
        "everything about a run id. The lane mints none: there is no run to look up, and a "
        "refusal about one would be about a thing that was never created."
    ),
}


def source_of(name: str) -> str:
    """One function's own source, and nothing else in the file.

    **SCOPED TO THE FUNCTION RATHER THAN GREPPED OVER THE MODULE, WHICH IS THE POINT.** A rule
    that searched the whole of ``main.py`` for ``run_preflight`` would pass its own named
    mutation, because ``_check`` calls it legitimately forty lines away and the word survives
    there whatever the lane verbs do. The same shape has been found seven times in this
    repository and it always reads as a working check.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(
        f"{name} is not in {MAIN.name}. The ruling in this file is about what that function may "
        "not call, so a rename has to be followed here rather than silently stopping the check."
    )


def called_by(name: str) -> set[str]:
    """Every name called inside one function, including through an attribute."""
    called: set[str] = set()
    for node in ast.walk(ast.parse(source_of(name))):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    return called


def test_every_function_the_ruling_covers_is_still_in_the_file() -> None:
    """Mutation: rename _run and leave this list behind.

    A rule that looked for a function that is not there any more would find nothing to object to
    and pass. source_of raises rather than returning an empty string, and this is what makes the
    raise reachable on the day somebody renames a verb.
    """
    for name in LANE_VERB_FUNCTIONS:
        assert source_of(name)


def test_no_lane_verb_calls_anything_the_ruling_names() -> None:
    """**THE WIRE. Mutation: call run_preflight from _run, which is one plausible line.**

    That line is the likeliest way this route stops being ungated, and it would look like an
    improvement in a diff: the lane would start refusing an unregistered repository, which is a
    real thing to refuse in the place that records runs. Here it is the defect, and the failure
    message names the verdicts that would arrive with it.
    """
    for function in LANE_VERB_FUNCTIONS:
        called = called_by(function)
        for gate, what_it_refuses in LANE_VERDICTS.items():
            assert gate not in called, (
                f"{function} calls {gate}, which brings {what_it_refuses} into a route that is "
                "ungated by design. If this is deliberate, the ruling at the top of this file "
                "has to change first and the owner's position is that it should not."
            )


def test_the_ruling_names_functions_that_exist_rather_than_words_that_do_not() -> None:
    """Mutation: misspell a gate in the ruling.

    A misspelled name is a rule that can never fire, and it reads exactly like one that passes.
    Each of these is a function somewhere in the package, so a rename that left this list behind
    is caught here rather than by nothing. Searched across the package rather than in a named
    file or two, because which module a gate lives in is not the property: three of these four
    are in cli/ and resolve_compute_profile_for_execution is in contracts/workload.py, and a
    list of files to look in would be a second thing to keep up to date.
    """
    package = PROJECT_ROOT / "src" / "edullm_platform"
    defined = {
        node.name
        for path in package.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
    }
    missing = sorted(gate for gate in LANE_VERDICTS if gate not in defined)

    assert not missing, (
        f"{missing} are named in the ruling and defined nowhere in the package, so the wire "
        "above is looking for words that cannot appear and would pass on any mutation."
    )


def test_the_lane_verbs_reach_the_lane_module_and_not_a_second_copy_of_it() -> None:
    """Mutation: build the run-instances argv inline in main.py.

    The launch is asserted flag by flag in tests/test_lane_launch.py against the argv builders. A
    verb that assembled its own would be untested in every particular that matters, and the two
    would drift on the first change.
    """
    assert "run_instances_argv" in called_by("_start_a_machine")
    assert "shell_session_argv" in called_by("_shell")
    assert "remote_command_argv" in called_by("_run")


def test_the_ruling_is_not_empty() -> None:
    """Mutation: empty LANE_VERDICTS.

    Both loops above are for-loops over this dict, and a for-loop over an empty dict passes
    without executing its body. That is the seventh instance of the check-that-cannot-fail shape
    found here, and it is the one that would make this whole file decorative.
    """
    assert len(LANE_VERDICTS) >= 4
    assert len(LANE_VERB_FUNCTIONS) >= 4
