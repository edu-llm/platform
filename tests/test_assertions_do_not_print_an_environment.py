"""Whether a failing assertion in this suite can print a process environment, secrets included.

**THE FAILURE THIS IS WRITTEN ABOUT, WHICH HAPPENED ON 2026-08-06.** A new test installed a
package twice and compared what uv reported, and it was written the obvious way::

    assert "uvt-probe v0.2.0" in uv(("tool", "list"), env=unpinned).stdout

``unpinned`` was ``dict(os.environ, UV_TOOL_DIR=...)``. pytest rewrites assertions in the
modules it collects, and when one fails it prints the repr of every intermediate value it
touched *including the arguments of the calls that produced them*. So the first time that
assertion was deliberately made to fail, the whole environment of the process running the
suite went into the log, and the line that came out held a live API key::

    E  +  where CompletedProcess(...) = uv(('tool', 'list'), env={'ANTHROPIC_CUSTOM_HEADERS': ...

That instance was found, restructured and never committed. This file exists because it is
not a property of that test. Any assertion whose expression hands an environment mapping to
a call has the same defect, it is invisible while the assertion passes, and it is revealed by
the one thing this repository does constantly: deliberately making a test fail to check that
it can.

**WHY IT IS ACUTE HERE.** A green suite never prints any of it, so nothing about the passing
state distinguishes a safe assertion from this one. The exposure arrives on a red run, which
is the run whose output somebody pastes into a pull request, and on a public repository a
failing job's log is world-readable and stays in the run's archive. Two other properties make
it worse than an ordinary leak. GitHub masks *registered secrets*, and a value that reached
the environment by any other route is not masked. And an agent inducing a failure to prove a
mutation is caught is doing the exact thing that triggers it.

**THE RULE READS WHAT PYTEST WOULD REPR AND NOTHING ELSE**, and every draft that got this
wrong got it wrong against real code in this repository. Too narrow and it sees only what the
first version saw, a mapping handed to a call under ``env=``, and walks past
``assert "HOME" in build_the_environment(...)``, where the mapping is the operand. Too broad
and it accuses correct tests three separate ways: a helper that builds a narrow environment
*inside itself* and returns a string never puts one in front of pytest; a name bound to a
``CompletedProcess`` is not an environment just because an environment was handed to the
subprocess that produced it; and reading bindings across a whole module confuses one name used
for two things in two functions. Each of the three is a test below, because the cheap way out
of a false positive is to widen a rule until it catches nothing, and one of the three arrived
by itself -- the rule's first run against a branch it had not been tuned on reported a test
that was right.

**A RULE THAT CANNOT FAIL IS WORTH NOTHING**, and this one finds nothing in the tree today,
which is the state most at risk of never having been able to fire.
:func:`test_the_rule_catches_the_assertion_that_printed_a_key` runs it against the code that
leaked, character for character, and
:func:`test_the_rule_catches_a_mapping_that_is_the_operand_rather_than_an_argument` holds the
one widening it needed. The three false positives are held by
:func:`test_a_helper_that_only_builds_an_environment_internally_is_not_a_finding`,
:func:`test_handing_an_environment_to_a_subprocess_is_not_a_finding` and
:func:`test_a_name_reused_for_a_string_elsewhere_in_the_module_is_not_a_finding`. And
:func:`test_the_rule_reads_the_committed_tree` requires the corpus to have found this
repository rather than an empty list.

**THE REMEDY IS ALWAYS THE SAME TWO LINES.** Read the value out into a name above the
assertion and compare the name. pytest then prints the string that was compared and has no
call left to expand, which is what ``tests/test_cli_install_command.py`` now does.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

#: The exact expression that leaked, kept as the rule's own fixture. Reduced to the two lines
#: that matter and otherwise as it was written, so what is proven here is that the rule
#: catches *that*, not a caricature of it built to be catchable.
THE_ASSERTION_THAT_LEAKED: Final = '''
def isolated_tool_home(home):
    return dict(os.environ, UV_TOOL_DIR=str(home / "dir"))


def test_it(tmp_path):
    unpinned = isolated_tool_home(tmp_path / "unpinned")
    assert "uvt-probe v0.2.0" in uv(("tool", "list"), env=unpinned).stdout
'''

#: The shape that is safe and must stay allowed: the mapping is built inside the helper, and
#: what comes back is a string. Taken from ``tests/test_phase3_execution.py``, which is the
#: real code this rule must not fire on.
THE_HELPER_THAT_IS_SAFE: Final = '''
def start_the_container_command(request, *, array_index):
    environment = container_environment(request)
    environment[FANOUT_INDEX_VARIABLE] = array_index
    started = subprocess.run(
        list(request["ContainerOverrides"]["Command"]),
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ.get("PATH", ""), **environment},
    )
    return started.stdout.strip()


def test_it(request):
    assert start_the_container_command(request, array_index="0") == "cell-0"
'''


#: The shape that made this rule accuse an innocent line on its own first run over the tree.
#: ``unpinned`` is an install command *string* in one test and an environment mapping in the
#: next, in one module, which is ``tests/test_cli_install_command.py`` as committed. The
#: second test is also the remedy this rule asks for -- the value read out into a name above
#: the assertion -- so a rule that reports either line here is unusable.
THE_NAME_REUSED_FOR_TWO_THINGS: Final = '''
def isolated_tool_home(home):
    return dict(os.environ, UV_TOOL_DIR=str(home))


def test_the_two_install_forms_differ_only_by_the_ref():
    unpinned = install_command(repository=PLATFORM_REPOSITORY)
    pinned = install_command(repository=PLATFORM_REPOSITORY, tag="v1.2.3")

    assert pinned == f"{unpinned}@v1.2.3"


def test_uv_upgrades_the_unpinned_install(tmp_path):
    unpinned = isolated_tool_home(tmp_path)
    listed = uv(("tool", "list"), env=unpinned).stdout

    assert "uvt-probe v0.2.0" in listed
'''

#: Handing an environment to a subprocess and then asserting over what it printed. Taken from
#: ``tests/test_verify_image_accelerator_cli.py``, which landed while this rule was being
#: written and which the rule reported on its first run against it. ``completed`` is a
#: ``CompletedProcess``: its repr carries args, returncode, stdout and stderr, and no
#: environment, so nothing here can print one.
THE_ENVIRONMENT_HANDED_TO_A_SUBPROCESS: Final = '''
def test_it(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )

    assert read_report(completed.stdout) == {"probe": "accelerator", "version": 1}
'''

#: The mapping as the operand rather than as an argument, which an earlier draft walked past.
THE_MAPPING_AS_THE_OPERAND: Final = '''
def isolated_tool_home(home):
    return dict(os.environ, UV_TOOL_DIR=str(home))


def test_it(tmp_path):
    assert "UV_TOOL_DIR" in isolated_tool_home(tmp_path)
'''


def tracked_python() -> list[Path]:
    """Every ``.py`` this repository commits, asked of git rather than of the filesystem.

    The same argument ``tests/test_cli_install_command.py`` makes for its own corpus. A walk
    reads whatever a working directory happens to hold, which differs per machine and is a
    failure nobody in CI can see; ``git ls-files`` is what a push carries and is the same set
    everywhere.
    """
    listing = subprocess.run(
        ("git", "ls-files", "-z", "*.py"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(
        path for name in listing.split("\0") if name if (path := PROJECT_ROOT / name).is_file()
    )


def _is_environment_mapping(node: ast.AST, returning: set[str]) -> bool:
    """Whether ``node`` *evaluates to* an environment mapping, rather than merely mentioning one.

    THE DISTINCTION THIS FUNCTION EXISTS FOR, WHICH COST THE RULE TWO FALSE POSITIVES BEFORE
    IT WAS DRAWN. ``dict(os.environ, PATH=...)`` is a copy of the environment.
    ``subprocess.run(..., env={**os.environ})`` mentions one and evaluates to a
    ``CompletedProcess``, whose repr carries args, returncode, stdout and stderr and no
    environment at all -- so a name bound to that is safe, and a rule that reads any mention
    as the thing itself accuses ``tests/test_verify_image_accelerator_cli.py`` for handing an
    environment to a subprocess, which is what handing an environment to a subprocess looks
    like. ``os.environ.get("PATH")`` and ``os.environ["PATH"]`` are one value each rather than
    the mapping, and are likewise not this.

    So the shapes are enumerated rather than searched for, and a mapping reached only as an
    argument to some other call is deliberately not one of them.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "environ"
    if isinstance(node, ast.Name):
        return node.id == "environ"
    if isinstance(node, ast.Starred):
        return _is_environment_mapping(node.value, returning)
    if isinstance(node, ast.Dict):
        # ``{**os.environ, "PATH": ...}``: an unpacked mapping has no key.
        return any(
            key is None and _is_environment_mapping(value, returning)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.IfExp):
        return any(
            _is_environment_mapping(branch, returning) for branch in (node.body, node.orelse)
        )
    if isinstance(node, ast.BoolOp):
        return any(_is_environment_mapping(value, returning) for value in node.values)
    if isinstance(node, ast.Call):
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        if isinstance(node.func, ast.Name):
            if node.func.id in returning:
                return True
            if node.func.id == "dict":
                return any(_is_environment_mapping(a, returning) for a in arguments)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "copy":
            return _is_environment_mapping(node.func.value, returning)
    return False


def _environ_returning(tree: ast.AST) -> set[str]:
    """Functions that hand an environment mapping *back*, which is the dangerous kind.

    Read off the ``return`` expressions rather than the whole body. A helper that mentions
    ``os.environ`` while returning a string has kept the mapping to itself, and treating the
    two the same is what made the first draft of this rule report nine safe assertions in
    ``tests/test_phase3_execution.py``.

    Settled rather than computed once, because one such helper may return the result of
    another and a single pass would see only the innermost. Two rounds is the common case and
    the loop stops as soon as a round adds nothing.
    """
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    returning: set[str] = set()
    while True:
        found = {
            node.name
            for node in functions
            if any(
                isinstance(inner, ast.Return)
                and inner.value is not None
                and _is_environment_mapping(inner.value, returning)
                for inner in ast.walk(node)
            )
        }
        if found <= returning:
            return returning
        returning |= found


SCOPES: Final = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Everything in ``scope`` except what belongs to a scope nested inside it.

    ``ast.walk`` crosses function boundaries, and reading bindings that way is what made this
    rule report ``tests/test_cli_install_command.py`` twice. That file binds ``unpinned`` to
    an install command *string* in one test and to an environment mapping in another, and a
    module-wide reading cannot tell the two apart -- so it accused the innocent one, which
    invites the next reader to rename a local or delete the rule.
    """
    own: list[ast.AST] = []
    frontier: list[ast.AST] = list(ast.iter_child_nodes(scope))
    while frontier:
        node = frontier.pop()
        own.append(node)
        if not isinstance(node, SCOPES):
            frontier.extend(ast.iter_child_nodes(node))
    return own


def _names_holding_an_environment(scope: ast.AST, returning: set[str]) -> set[str]:
    """Names bound in ``scope`` to an environment mapping, directly or from ``returning``."""
    holding: set[str] = set()
    for node in _own_nodes(scope):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, ast.For):
            targets, value = [node.target], node.iter
        else:
            continue
        if not _is_environment_mapping(value, returning):
            continue
        for target in targets:
            holding |= {inner.id for inner in ast.walk(target) if isinstance(inner, ast.Name)}
    return holding


def _why_it_would_print(node: ast.AST, returning: set[str], holding: set[str]) -> str | None:
    """The reason ``node`` puts an environment mapping in front of pytest's repr, or None.

    Anywhere under the asserted expression is the test, not any particular position in it.
    An earlier draft looked only at the arguments of calls, which caught the assertion that
    leaked -- it handed one to ``env=`` -- and would have walked straight past
    ``assert "HOME" in isolated_tool_home(tmp_path)``, where the mapping is not an argument
    to anything but is the operand pytest prints.
    """
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name) and inner.id in holding:
            return f"names `{inner.id}`, which holds an environment mapping"
        if isinstance(inner, ast.Call):
            if any(keyword.arg == "env" for keyword in inner.keywords):
                return "hands `env=` to a call inside the assertion"
            if isinstance(inner.func, ast.Name) and inner.func.id in returning:
                return f"calls `{inner.func.id}()`, which returns an environment mapping"
        if _is_environment_mapping(inner, returning):
            return "evaluates an environment mapping inside the assertion"
    return None


def assertions_that_would_print_an_environment(source: str) -> list[tuple[int, str]]:
    """Every assertion in ``source`` whose failure would print a process environment.

    Four ways it happens, and all four are one question -- does an environment mapping end
    up among the values pytest reprs. It is named outright; it is a local name bound to one;
    it is handed to a call under the ``env`` keyword every process API spells it with; or it
    is what a call in the assertion hands back.
    """
    tree = ast.parse(source)
    returning = _environ_returning(tree)

    found: list[tuple[int, str]] = []

    def walk_scope(scope: ast.AST, inherited: set[str]) -> None:
        holding = inherited | _names_holding_an_environment(scope, returning)
        for node in _own_nodes(scope):
            if isinstance(node, ast.Assert):
                for part in [node.test, *([node.msg] if node.msg is not None else [])]:
                    why = _why_it_would_print(part, returning, holding)
                    if why is not None:
                        found.append((node.lineno, why))
                        break
            elif isinstance(node, SCOPES):
                walk_scope(node, holding)

    walk_scope(tree, set())
    return sorted(found)


def test_no_tracked_assertion_can_print_an_environment() -> None:
    """The rule itself, over everything this repository commits.

    Mutation: write the leaking form back into any test. The remedy is always the same shape
    and it is two lines: read the value out into a name above the assertion and compare the
    name. pytest then prints the string it compared and has no call left to expand.
    """
    offenders = [
        f"{path.relative_to(PROJECT_ROOT)}:{line}  {why}"
        for path in tracked_python()
        for line, why in assertions_that_would_print_an_environment(
            path.read_text(encoding="utf-8", errors="replace")
        )
    ]

    assert not offenders, (
        "a failing one of these would print the whole environment of the process running "
        "the suite, and on a public repository that log stays readable:\n  "
        + "\n  ".join(offenders)
        + "\nAssign the value to a name on the line above and assert over the name."
    )


def test_the_rule_catches_the_assertion_that_printed_a_key() -> None:
    """**THE HALF THAT STOPS THIS BEING A CHECK THAT CANNOT FAIL.**

    The rule above finds nothing today, so on its own it is indistinguishable from a rule
    scoped until it matches nothing, which is the shape this repository has now found more
    than a dozen times. Here it is run against the assertion that actually leaked.
    """
    found = assertions_that_would_print_an_environment(THE_ASSERTION_THAT_LEAKED)

    assert found, (
        "the rule no longer recognises the assertion it was written for, so it is passing "
        "over the tree because it can no longer see anything"
    )
    assert "env=" in found[0][1]


def test_the_rule_catches_a_mapping_that_is_the_operand_rather_than_an_argument() -> None:
    """Mutation: look only at the arguments of the calls in the assertion.

    That draft caught the assertion that leaked, because that one handed the mapping to
    ``env=``, and it is the reason this second case is held separately. Here the mapping is
    not an argument to anything -- it is the right-hand side of the ``in`` -- and pytest
    prints it as the operand it compared against.
    """
    found = assertions_that_would_print_an_environment(THE_MAPPING_AS_THE_OPERAND)

    assert [why for _, why in found] == [
        "calls `isolated_tool_home()`, which returns an environment mapping"
    ]


def test_a_name_reused_for_a_string_elsewhere_in_the_module_is_not_a_finding() -> None:
    """**THE FALSE POSITIVE THIS RULE ALREADY MADE ONCE, HELD SO IT CANNOT MAKE IT AGAIN.**

    Mutation: collect the environment-holding names per module instead of per scope. The
    rule then reported two assertions in ``tests/test_cli_install_command.py`` comparing
    install command strings, because a *different* function in that file binds the same name
    to an environment. Both lines are safe, and a rule that names them is one somebody turns
    off. The second function here is additionally the remedy the rule recommends, so it must
    read as clean for the advice to be followable.
    """
    assert assertions_that_would_print_an_environment(THE_NAME_REUSED_FOR_TWO_THINGS) == []


def test_handing_an_environment_to_a_subprocess_is_not_a_finding() -> None:
    """**THE THIRD FALSE POSITIVE, AND THE ONE THAT WOULD HAVE MADE THE RULE UNSHIPPABLE.**

    Mutation: treat any expression *mentioning* ``os.environ`` as an environment mapping.
    That draft accused ``tests/test_verify_image_accelerator_cli.py``, which landed from
    another branch while this file was being written -- so the rule's very first contact with
    code it had not been tuned against produced a failure on a correct test.

    ``env={**os.environ, ...}`` is how a subprocess is given an environment, it is the whole
    point of the ``env`` parameter, and the name it binds is a ``CompletedProcess``, whose
    repr has no environment in it. A rule that reports this reports ordinary practice, and it
    would have been switched off within a day by somebody who was right to switch it off.
    """
    assert assertions_that_would_print_an_environment(THE_ENVIRONMENT_HANDED_TO_A_SUBPROCESS) == []


def test_a_helper_that_only_builds_an_environment_internally_is_not_a_finding() -> None:
    """Mutation: flag every function that mentions ``os.environ`` anywhere in its body.

    That was the first draft and it reported nine assertions in
    ``tests/test_phase3_execution.py``, all safe: the helper builds a deliberately narrow
    environment of ``PATH`` plus the manifest's own variables, keeps it inside itself, and
    returns a string. Widening a rule until the real code trips it is how a rule gets
    switched off, so the distinction is held here rather than left to whoever next reads a
    false positive.
    """
    assert assertions_that_would_print_an_environment(THE_HELPER_THAT_IS_SAFE) == []


def test_the_rule_reads_the_committed_tree() -> None:
    """Guards the corpus, because a rule over an empty list reports success forever.

    Named files rather than a count. A count drifts and a rename that emptied the corpus
    would still satisfy one.
    """
    names = {path.name for path in tracked_python()}

    assert {"conftest.py", "test_phase3_execution.py", "test_cli_install_command.py"} <= names
