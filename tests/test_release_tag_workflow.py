"""The workflow that cuts releases, and the check that makes it possible for it to.

THIS FILE EXISTS BECAUSE THAT WORKFLOW FAILED ON EVERY MERGE FOR SEVEN MERGES AND NOTHING
NOTICED. It computed the next patch version, committed the bump to ``pyproject.toml`` and
``uv.lock``, and pushed that commit to ``main``; branch protection refuses a push to
``main``, so every run died on the same line:

    remote: - Changes must be made through a pull request.
    remote: - 2 of 2 required status checks are expected.

``releases/latest`` went on naming a tag cut before all of them while seven merges of CLI and
configuration work sat unreleased. The install line researchers are handed pins a tag, so the
automation written to keep that line current was the thing keeping it stale -- and the CLI's
own staleness probe, comparing an install against that same endpoint, told everybody they
were current the whole time. There was no test on the file at all, which is why reading it
was the only way to find out.

So the properties below are the ones worth holding. **Nothing here may write to a branch**:
the tag and the release are the only writes, neither ref is protected, and the protection on
``main`` is not weakened, bypassed or exempted to make this work. **The version has to be
declared before the merge rather than after it**, because it is a literal in a file and only
a pull request may put a commit on ``main`` -- which is what ``ci.yml``'s last step is for,
and why it is tested here beside the workflow it serves rather than in
``tests/test_ci_workflow.py`` with the rest of that file's arrangement. And **the trigger has
to be the surface an installed CLI can observe and nothing wider**, which is the part with
teeth, because the cost of getting it wrong is not a failed run. It is thirty-five people
told to re-install for a change to a proof generator, until the day they stop reading it.

The behavioural cases run the ``run:`` bodies as the runner runs them, against real git
repositories with a real remote and against this repository's real tools. A workflow
asserted only by reading its YAML is how the push to ``main`` survived review in the first
place.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from workflow_support import (
    PROJECT_ROOT,
    WORKFLOWS_ROOT,
    load_workflow,
    only_job,
    run_step_script,
    shell_syntax_without_heredoc_bodies,
    step,
    unreal_context_references,
    write_stub,
)

from edullm_platform.cli import configuration as cli_configuration
from edullm_platform.cli.actions import PLATFORM_REPOSITORY
from edullm_platform.cli.release import install_command

sys.path.insert(0, str(PROJECT_ROOT / "tools"))
from release_paths import (
    ReleasePathsUnreadableError,
    pathspec_for,
    release_paths,
    trigger_paths,
)

WORKFLOW_PATH = WORKFLOWS_ROOT / "release-tag.yml"
CI_PATH = WORKFLOWS_ROOT / "ci.yml"

#: The steps, by the names the workflows give them. Named here so a rename is one failure
#: with a readable message rather than an assertion error inside a helper.
DECIDE_STEP = "Decide the version this merge releases"
CUT_STEP = "Cut the tag and the release"
GUARD_STEP = "A change a release must carry declares a version no release has"

#: What the bump costs, spelled once. Both failure messages have to carry it -- the one
#: before the merge and the one after -- because a failure that says only that something is
#: wrong is one whose reader goes looking for an administrator.
BUMP_COMMAND = "tools/next_version.py --bump"

#: The package that is the CLI. Every module in it is part of the observable surface whether
#: or not ``main.py`` imports it today, which is what makes the directory glob in the trigger
#: honest rather than convenient.
CLI_PACKAGE = "src/edullm_platform/cli"

#: Where the CLI's modules live, as an import prefix.
DISTRIBUTION_PACKAGE = "edullm_platform"


# --------------------------------------------------------------------------------------
# What an installed CLI can observe
# --------------------------------------------------------------------------------------


def tracked_files() -> frozenset[str]:
    """Every file git knows about, which is the set Actions matches a push against."""
    listed = subprocess.run(
        ("git", "ls-files"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return frozenset(line for line in listed.stdout.splitlines() if line)


def module_path(module: str) -> Path | None:
    for candidate in (
        PROJECT_ROOT / "src" / (module.replace(".", "/") + ".py"),
        PROJECT_ROOT / "src" / module.replace(".", "/") / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def imported_by(module: str, source: Path) -> Iterator[str]:
    """Every module of this distribution that executing ``source`` would import.

    Function-level imports count. A module imported lazily is still a module whose contents
    decide what the CLI answers, and the ones here are lazy for start-up cost rather than
    for optionality.
    """
    package = module if source.name == "__init__.py" else module.rsplit(".", 1)[0]
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rsplit(".", 1)[0]
                name = f"{base}.{node.module}" if node.module else base
            else:
                name = node.module or ""
            if not name.startswith(DISTRIBUTION_PACKAGE):
                continue
            yield name
            # `from x import y` reaches a submodule when y is one, and reaches a name in x
            # when it is not. Both spellings are offered here and module_path discards the
            # one that names nothing on disk.
            yield from (f"{name}.{alias.name}" for alias in node.names)


def cli_import_closure() -> frozenset[str]:
    """Every file in this repository that importing the CLI executes.

    Derived rather than listed, which is the whole point of it. A trigger written out by
    hand is right on the day it is written and wrong the first time somebody adds an import,
    and wrong in the direction nothing notices: the release is not cut, ``releases/latest``
    goes on naming the older tag, and every installed CLI compares equal to it and is told
    it is current.
    """
    found: dict[str, Path] = {}
    queue = ["edullm_platform.cli", "edullm_platform.cli.__main__", "edullm_platform.cli.main"]
    while queue:
        module = queue.pop()
        if module in found:
            continue
        source = module_path(module)
        if source is None:
            continue
        found[module] = source
        # A subpackage import executes every ``__init__`` above it, and those files are as
        # able to change an answer as any other.
        parts = module.split(".")
        queue.extend(".".join(parts[:depth]) for depth in range(1, len(parts)))
        queue.extend(imported_by(module, source))
    return frozenset(path.relative_to(PROJECT_ROOT).as_posix() for path in found.values())


@pytest.fixture
def configuration_files(monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    """The configuration files the CLI opens, by watching it open them.

    Read off the loader rather than off a list, because a list is what went wrong. The
    workflow tested whether anything under ``config/`` had moved, and that directory holds a
    capacity table, a set of execution targets and a folder of generated reports that the
    CLI never reads. ``v0.2.1`` announced that the reviewed configuration had moved when the
    only file that changed was ``config/capacity.yaml``.
    """
    opened: list[str] = []

    def record(path: Path, model: object) -> object:
        opened.append(Path(path).name)
        return object()

    monkeypatch.setattr(cli_configuration, "load_yaml", record)
    cli_configuration.load_reviewed_configuration(PROJECT_ROOT / "config")
    return tuple(opened)


def observable_surface(configuration_files: tuple[str, ...]) -> frozenset[str]:
    """Every tracked file a change to which can change what an installed CLI answers."""
    cli_package = {path for path in tracked_files() if path.startswith(f"{CLI_PACKAGE}/")}
    return frozenset(
        cli_package
        | set(cli_import_closure())
        | {f"config/{name}" for name in configuration_files}
        # The version is what the tag is cut from and what the staleness probe compares, so
        # a change that moves it is by definition a release even when it moves nothing else.
        | {"pyproject.toml"}
    )


def matched_by(pattern: str, path: str) -> bool:
    """One Actions path filter against one repository path.

    Two shapes only, and ``pathspec_for`` is what refuses a third. Actions supports far more
    than this; supporting more here would mean reimplementing its matcher, and a matcher that
    is subtly not Actions' own reports a trigger as correct that is not.
    """
    if pattern.endswith("/**"):
        return path.startswith(pattern[: -len("**")])
    return path == pattern


def files_the_trigger_matches() -> frozenset[str]:
    patterns = trigger_paths(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return frozenset(
        path for path in tracked_files() if any(matched_by(item, path) for item in patterns)
    )


def test_the_trigger_is_the_surface_an_installed_cli_can_observe(
    configuration_files: tuple[str, ...],
) -> None:
    """THE CASE THIS WHOLE FILE IS FOR. Mutation: add or drop one path.

    The trigger used to be ``src/edullm_platform/**``, ``config/**`` and ``pyproject.toml``,
    which is the entire platform and the entire control plane, and the workflow's own header
    said in the same breath that a merge changing nothing an installed CLI would answer
    differently should not cut a release. Twelve of the sixteen merges before the correction
    matched it; six of them moved a proof generator, a capacity table, a checkpoint reader, a
    run comparator or an account reader, and each one would have moved ``releases/latest``.

    Both directions fail here and both matter. Too narrow and a change reaches nobody. Too
    wide and the warning cries wolf, which is worse, because the first is noticed by whoever
    is waiting for the change and the second is noticed by nobody until people stop reading.
    """
    expected = observable_surface(configuration_files)
    matched = files_the_trigger_matches()

    assert matched == expected, (
        "the release trigger and the CLI's observable surface have come apart.\n"
        f"  in the trigger and not the surface: {sorted(matched - expected)}\n"
        f"  in the surface and not the trigger: {sorted(expected - matched)}\n"
        "Paste this over the `paths:` list in .github/workflows/release-tag.yml:\n\n"
        + "\n".join(f'      - "{path}"' for path in sorted_trigger(expected))
    )


def sorted_trigger(surface: frozenset[str]) -> list[str]:
    """The surface written the way the trigger writes it, ready to paste.

    The CLI package collapses to one directory pattern and everything else is itself, in the
    order a reader of the workflow would want: the code, then the configuration, then the
    file the version lives in.
    """
    outside = sorted(
        path
        for path in surface
        if not path.startswith(f"{CLI_PACKAGE}/")
        and not path.startswith("config/")
        and path != "pyproject.toml"
    )
    configuration = sorted(path for path in surface if path.startswith("config/"))
    return [f"{CLI_PACKAGE}/**", *outside, *configuration, "pyproject.toml"]


def test_every_pattern_in_the_trigger_matches_a_file_that_exists() -> None:
    """Mutation: misspell one path.

    Set equality above would not catch it on its own. A pattern naming a file that is not
    there matches nothing, contributes nothing, and reads as correct right up until somebody
    edits the file it was meant to name.
    """
    tracked = tracked_files()
    patterns = trigger_paths(WORKFLOW_PATH.read_text(encoding="utf-8"))
    unmatched = [
        pattern for pattern in patterns if not any(matched_by(pattern, path) for path in tracked)
    ]

    assert unmatched == [], f"these trigger paths match no tracked file: {unmatched}"


def test_the_trigger_holds_no_module_the_cli_does_not_reach() -> None:
    """The direction that cries wolf, named by the modules it used to include.

    Every one of these is a real module in ``src/edullm_platform`` that no import from the
    CLI reaches, and every one of them was inside the old trigger. Asserted by name as well
    as by the set comparison above, because a reader of a failure wants to know which kind of
    mistake was made and "the sets differ" does not say.
    """
    matched = files_the_trigger_matches()
    unreachable = {
        "src/edullm_platform/checkpoints.py",
        "src/edullm_platform/phase4_criteria.py",
        "src/edullm_platform/proof_generator.py",
        "src/edullm_platform/run_comparison.py",
        "src/edullm_platform/substrate.py",
        "config/capacity.yaml",
    }

    assert unreachable <= tracked_files(), "this case is naming modules that no longer exist"
    assert not (matched & unreachable)


@pytest.mark.slow
def test_importing_the_cli_executes_nothing_the_trigger_has_not_heard_of() -> None:
    """The half a syntax tree cannot see. Mutation: reach a module through importlib.

    The closure above is read out of import statements, so a module reached by name at run
    time is invisible to it. This imports the CLI in a cold process and asks what actually
    ended up in ``sys.modules``, which is the only way to catch that.
    """
    program = (
        "import sys, json; import edullm_platform.cli.main; "
        "print(json.dumps(sorted(name for name in sys.modules "
        f"if name.startswith('{DISTRIBUTION_PACKAGE}'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr

    imported = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for name in json.loads(completed.stdout)
        if (path := module_path(name)) is not None
    }

    assert imported <= files_the_trigger_matches()


def test_the_release_note_asks_about_the_files_the_cli_actually_opens(
    configuration_files: tuple[str, ...],
) -> None:
    """Mutation: put ``config/`` back in the headline's diff.

    That is what shipped. ``v0.2.1`` says the reviewed configuration moved and it did not:
    the only file that changed between the two tags was ``config/capacity.yaml``, which
    nothing in the CLI opens. The sentence is the most useful line in a release note and it
    is the one a researcher acts on, so a false one costs everybody who read it a re-install
    and costs the true one its credibility.
    """
    declared = str(step(release_job(), CUT_STEP)["env"]["CONFIGURATION_FILES"]).split()

    assert sorted(declared) == sorted(f"config/{name}" for name in configuration_files)
    assert len(configuration_files) == 6, "the loader opens a different number of files now"


# --------------------------------------------------------------------------------------
# Reading the files
# --------------------------------------------------------------------------------------


def release_job() -> dict[str, object]:
    return only_job(load_workflow(WORKFLOW_PATH))


def checks_job() -> dict[str, object]:
    job = load_workflow(CI_PATH)["jobs"]["checks"]
    assert isinstance(job, dict)
    return job


def script_of(job: dict[str, object], name: str) -> str:
    return str(step(job, name)["run"])


def commands_in(script: str) -> list[str]:
    """The lines that run, without the lines that explain or the lines that are printed.

    Two kinds of text in these scripts are not commands and both name commands. Comments
    are most of the file and they quote what they argue about -- the ``git commit`` the
    tagger identity is needed for, the push that used to be here. Heredoc bodies are the
    failure messages, and a message whose whole job is to tell somebody to run
    ``next_version.py --bump`` would read to a rule about the raw text as the workflow
    running it.
    """
    stripped = (line.strip() for line in shell_syntax_without_heredoc_bodies(script).splitlines())
    return [line for line in stripped if line and not line.startswith("#")]


def every_release_command() -> list[str]:
    steps = release_job()["steps"]
    assert isinstance(steps, list)
    return [
        command
        for item in steps
        if isinstance(item, dict) and "run" in item
        for command in commands_in(str(item["run"]))
    ]


def test_every_expression_names_something_that_actually_exists() -> None:
    assert unreal_context_references(WORKFLOW_PATH) == []


def test_nothing_here_commits_anything_or_pushes_a_branch() -> None:
    """THE CASE THIS FILE WAS STARTED FOR. Mutation: put the bump commit back.

    Every failed run did the same three things -- bump, commit, ``git push origin
    HEAD:main`` -- and the third is refused by branch protection. Asserted as "the only
    push is the tag" rather than as "``HEAD:main`` does not appear", because the ways to
    write a push to a protected branch are not enumerable and the ways to push a tag are.
    """
    commands = every_release_command()
    pushes = [command for command in commands if "git push" in command]

    assert pushes == ['git push origin "${TAG}"']
    assert [command for command in commands if "git commit" in command] == []
    assert [command for command in commands if "next_version.py --bump" in command] == []


def test_the_only_write_it_is_given_is_the_one_it_needs() -> None:
    # contents: write covers the tag and the release and nothing else here wants a
    # permission. A file that grew one would be reaching for something it should not.
    workflow = load_workflow(WORKFLOW_PATH)

    assert workflow["permissions"] == {"contents": "write"}
    assert "permissions" not in release_job()


def test_a_release_and_not_only_a_tag_is_what_gets_cut() -> None:
    """Mutation: drop the ``gh release create`` and leave the tag.

    ``edullm submit`` probes ``repos/.../releases/latest``, which a bare tag does not
    create: a repository with tags and no releases answers 404, the probe reads that as
    "could not ask", and every install is told nothing rather than told it is behind.
    """
    release_commands = [command for command in every_release_command() if "gh release" in command]
    creates = [command for command in release_commands if "gh release create" in command]
    views = [command for command in release_commands if "gh release view" in command]

    assert len(creates) == 1
    # The other one finds the previous release, and it has to stay a read that cannot fail
    # the run: the first release of all has nothing to find.
    assert len(views) == 1
    assert "|| true" in views[0]
    assert len(release_commands) == 2


def test_it_can_be_started_by_hand_as_well_as_by_a_merge() -> None:
    """The handle it did not have on the morning seven releases were missing.

    With only the push trigger, cutting a release the automation missed means a commit to
    ``main`` whose only purpose is to touch one of the trigger paths -- which is a pull
    request, a review and a merge to make up for a workflow that failed.
    """
    triggers = load_workflow(WORKFLOW_PATH)["on"]

    assert "workflow_dispatch" in triggers
    assert triggers["push"]["branches"] == ["main"]


def test_two_merges_a_minute_apart_are_serialized_rather_than_cancelled() -> None:
    # Cancelling the first would abandon a release for a merge that is already on main,
    # and nothing afterwards would notice it never happened.
    workflow = load_workflow(WORKFLOW_PATH)

    assert workflow["concurrency"] == {"group": "release-tag", "cancel-in-progress": False}


def test_the_checkout_brings_the_tags_and_the_history_the_decision_needs() -> None:
    """Mutation: take the ``with:`` off and accept the shallow default.

    Two things need it and both fail quietly. The decision is "does this tag exist", and a
    checkout with no tags answers no every time -- so the workflow would try to cut a tag
    that is already there on every merge. The notes then ask what has moved since the
    previous release, which needs the history between that tag and here.
    """
    steps = release_job()["steps"]
    assert isinstance(steps, list)
    checkouts = [item for item in steps if "checkout" in str(item.get("uses", ""))]

    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {"fetch-depth": 0}


def test_the_guard_reads_the_one_list_rather_than_carrying_a_second_one() -> None:
    """Mutation: write the paths into ``ci.yml`` as well.

    They were two lists, restated because Actions has no way to share them and held together
    by a test. That works until somebody adds a path to the trigger and runs the suite, which
    fails, and they fix it by editing the test. One list read out of the file that owns it
    cannot come apart at all.
    """
    guard = script_of(checks_job(), GUARD_STEP)

    assert "tools/release_paths.py" in guard
    assert "src/edullm_platform" not in guard, (
        "ci.yml is naming a release path of its own, which is the second copy this removed"
    )


def test_the_paths_tool_answers_with_the_workflows_own_list() -> None:
    """Mutation: point the tool at a different file, or let it translate a glob loosely.

    An Actions path filter and a git pathspec look identical for most inputs and are not the
    same language. ``src/edullm_platform/cli/**`` has to reach git as a directory, which git
    is unambiguous about, and anything the tool cannot translate has to be refused rather
    than passed through: a pathspec that matches nothing reports every change as touching
    nothing and passes everything.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert release_paths(text) == [pathspec_for(pattern) for pattern in trigger_paths(text)]
    assert f"{CLI_PACKAGE}/**" in trigger_paths(text)
    assert CLI_PACKAGE in release_paths(text)
    with pytest.raises(ReleasePathsUnreadableError, match="wildcard"):
        pathspec_for("src/**/*.py")


def test_a_workflow_with_no_trigger_list_is_refused_rather_than_read_as_empty() -> None:
    """Mutation: return an empty list when the key is missing.

    An empty pathspec list is not "nothing qualifies", it is "everything", because
    ``git diff -- `` with no paths is the whole diff. The other way round is worse: a guard
    that reads no paths and finds no touched files passes every pull request, which is the
    same silence the seven failed merges had.
    """
    with pytest.raises(ReleasePathsUnreadableError, match="no on.push.paths"):
        trigger_paths("name: x\non:\n  push:\n    branches: [main]\n")
    with pytest.raises(ReleasePathsUnreadableError, match="not a YAML mapping"):
        trigger_paths("- a list\n")


def test_the_unquoted_on_key_yaml_reads_as_a_boolean_is_still_found() -> None:
    """``on`` is YAML 1.1's ``true``, which is the one place a workflow and YAML disagree.

    Every workflow in this repository writes it unquoted, so a reader that only looked for
    the string key would find no trigger in any of them and refuse the lot.
    """
    quoted = 'name: x\n"on":\n  push:\n    paths: ["a.py"]\n'
    unquoted = "name: x\non:\n  push:\n    paths: [a.py]\n"

    assert trigger_paths(quoted) == trigger_paths(unquoted) == ["a.py"]


def test_the_guard_reads_pull_requests_and_stays_out_of_the_merge() -> None:
    """Mutation: drop the ``if:``.

    On a push to ``main`` the comparison is against the previous commit rather than
    against a base, and the declared version is the one the merge just released -- so the
    step would fail the merge it was meant to protect, in a job that is a required check
    for everybody else's pull request.
    """
    guard = step(checks_job(), GUARD_STEP)

    assert guard["if"] == "github.event_name == 'pull_request'"


def test_the_ci_checkout_reaches_the_base_the_guard_diffs_against() -> None:
    # actions/checkout leaves the merge commit at HEAD on a pull request, so HEAD^1 is the
    # base branch -- but only if its parents were fetched. At the shallow default the guard
    # finds no base, and it is written to pass rather than block when it cannot tell.
    steps = checks_job()["steps"]
    assert isinstance(steps, list)
    checkouts = [item for item in steps if "checkout" in str(item.get("uses", ""))]

    assert len(checkouts) == 1
    assert checkouts[0]["with"]["fetch-depth"] >= 2


def test_the_guard_runs_after_the_checks_somebody_is_waiting_on() -> None:
    # Failing it means one command and one commit. Somebody who has to run it would rather
    # learn that alongside the test results than instead of them, and the step is the
    # cheapest in the job so its position costs nothing.
    steps = checks_job()["steps"]
    assert isinstance(steps, list)

    assert steps[-1]["name"] == GUARD_STEP


def test_the_pull_request_template_names_the_three_sizes_and_the_command() -> None:
    """The prompt, which is not the mechanism and must not be mistaken for one.

    The check reads ``pyproject.toml`` and nothing else, so the template cannot be a second
    source of truth and cannot rot into one. What it is for is the moment before the mistake:
    somebody about to open a pull request that changes what an installed CLI answers, who has
    not thought about whether it is a minor. It has to name all three sizes, or it teaches
    that patch is the only one there is, which is the state this came from.
    """
    template = (PROJECT_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")

    for size in ("patch", "minor", "major"):
        assert f"{BUMP_COMMAND} {size}" in template


# --------------------------------------------------------------------------------------
# Running the steps
# --------------------------------------------------------------------------------------


def git(repository: Path, *arguments: str) -> str:
    finished = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return finished.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A checkout with an origin behind it, because these steps push and read remote refs.

    A bare repository rather than a fake remote: ``git push origin <tag>`` is the operation
    under test on the network side and it is exactly itself against a path.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(("git", "init", "--quiet", "--bare", str(origin)), check=True)
    work = tmp_path / "work"
    subprocess.run(
        ("git", "clone", "--quiet", str(origin), str(work)), check=True, capture_output=True
    )
    git(work, "config", "user.email", "nobody@example.invalid")
    git(work, "config", "user.name", "nobody")
    # run_step_script leaves the body it ran in the working directory, and these cases
    # commit after running one. Excluded rather than cleaned up, so a case that adds a
    # step cannot accidentally commit it and change the diff it is asserting about.
    (work / ".git" / "info" / "exclude").write_text("step.sh\n", encoding="utf-8")
    return work


def commit(repository: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "--message", message)


def install_real_tooling(repository: Path, *, version: str) -> None:
    """Put this repository's own tools and trigger list into the fixture, and commit them.

    Copied rather than stubbed. What the guard does with the answers is this file's subject,
    but the answers themselves come from ``next_version.py`` reading a real
    ``pyproject.toml`` and from ``release_paths.py`` reading the real trigger, and a stub
    that agreed with neither would let the whole step pass while the shipped one failed on
    the first pull request that ran it.
    """
    (repository / "tools").mkdir(exist_ok=True)
    for tool in ("next_version.py", "release_paths.py"):
        shutil.copy(PROJECT_ROOT / "tools" / tool, repository / "tools" / tool)
    workflows = repository / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    shutil.copy(WORKFLOW_PATH, workflows / WORKFLOW_PATH.name)
    commit(
        repository,
        "the tools the checks run",
        {"pyproject.toml": f'[project]\nname = "edullm-platform"\nversion = "{version}"\n'},
    )


def uv_running(directory: Path) -> Path:
    """A ``uv`` that runs what ``uv run --frozen python ...`` would run.

    The runner has a synced virtualenv by the time this step runs and the sandbox has none,
    so the three words in front of the script are what has to be absorbed. The interpreter is
    this suite's own, which is the one that has the dependencies the tools import.
    """
    return write_stub(directory, "uv", f'shift 3\nexec "{sys.executable}" "$@"\n')


def python3_saying(directory: Path, version: str) -> Path:
    """A ``python3`` that answers what ``tools/next_version.py`` would answer.

    Stubbed rather than real because the runner's ``python3`` is not this project's
    interpreter and the tool needs ``tomllib``, which the system one here predates. What
    the tool does with a real ``pyproject.toml`` is ``tests/test_next_version.py``'s
    subject; what the workflow does with the answer is this one's.
    """
    return write_stub(directory, "python3", f'echo "{version}"\n')


def run_decide(repository: Path, tmp_path: Path, *, declared: str) -> tuple[int, str, str, str]:
    stub_bin = tmp_path / "bin"
    python3_saying(stub_bin, declared)
    output = tmp_path / "step-output"
    summary = tmp_path / "step-summary"
    output.write_text("", encoding="utf-8")
    summary.write_text("", encoding="utf-8")

    finished = run_step_script(
        script_of(release_job(), DECIDE_STEP),
        cwd=repository,
        env={
            "HOME": str(tmp_path),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        stub_bin=stub_bin,
    )
    return (
        finished.returncode,
        output.read_text(encoding="utf-8"),
        summary.read_text(encoding="utf-8"),
        finished.stdout + finished.stderr,
    )


def test_a_version_no_tag_carries_yet_is_the_version_this_merge_releases(
    repository: Path, tmp_path: Path
) -> None:
    commit(repository, "a merge", {"src/edullm_platform/cli/main.py": "code\n"})

    code, output, summary, _ = run_decide(repository, tmp_path, declared="0.2.2")

    assert code == 0
    assert "version=0.2.2" in output
    assert "tag=v0.2.2" in output
    assert summary == ""


def test_a_version_already_released_stops_the_run_and_says_what_clears_it(
    repository: Path, tmp_path: Path
) -> None:
    """THE ONE REMAINING WAY THIS FAILS, AND IT HAS TO FAIL LOUDLY.

    Mutation: skip quietly when the tag exists.

    A merge that changed the CLI or the configuration and still declares a released
    version means the pull-request check was bypassed -- ``enforce_admins`` is off here --
    or two pull requests bumped to the same number and raced. Either way real work is
    unreleased. Skipping leaves ``releases/latest`` naming the older tag, every installed
    CLI comparing equal to it, and everybody told they are current: the false "you are
    current" the whole subsystem exists to prevent, arrived by a quieter road.

    So it is red, and what makes red acceptable is that the message is the remedy. It has
    to name the command, because nothing after the merge can bump the version and a reader
    who cannot see that goes looking for an administrator instead.
    """
    commit(repository, "a merge", {"config/policy.yaml": "rules\n"})
    git(repository, "tag", "--annotate", "v0.2.2", "--message", "v0.2.2")

    code, output, summary, said = run_decide(repository, tmp_path, declared="0.2.2")

    assert code == 1
    assert output == "", "a failed decision must not leave a version for the next step"
    assert "::error::" in said
    assert BUMP_COMMAND in summary
    assert "v0.2.2" in summary
    assert "cannot write to `main`" in summary


def test_the_failure_offers_all_three_sizes_and_not_only_a_patch(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: name only ``--bump``.

    Whoever reads this message is deciding how big the change they just merged was, and a
    message offering one option answers that for them. The version was a patch and only a
    patch for the life of the project because the only command anybody was ever shown
    produced one.
    """
    commit(repository, "a merge", {"config/policy.yaml": "rules\n"})
    git(repository, "tag", "--annotate", "v0.2.2", "--message", "v0.2.2")

    _, _, summary, _ = run_decide(repository, tmp_path, declared="0.2.2")

    for size in ("patch", "minor", "major"):
        assert f"{BUMP_COMMAND} {size}" in summary


def run_cut(
    repository: Path, tmp_path: Path, *, tag: str, latest: str
) -> tuple[int, str, str, str]:
    stub_bin = tmp_path / "bin"
    calls = tmp_path / "gh-calls"
    # `gh release view --json tagName --jq .tagName` is a read of the releases API, which is
    # the only thing here that cannot be answered out of the git repository.
    write_stub(
        stub_bin,
        "gh",
        f'printf "%s\\n" "$*" >>"{calls}"\nif [ "${{2:-}}" = "view" ]; then echo "{latest}"; fi\n',
    )
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)

    finished = run_step_script(
        script_of(release_job(), CUT_STEP),
        cwd=repository,
        env={
            "HOME": str(tmp_path),
            "GH_TOKEN": "not-a-token",
            "TAG": tag,
            "GITHUB_REPOSITORY": PLATFORM_REPOSITORY,
            "RUNNER_TEMP": str(runner_temp),
            **{
                str(name): str(value)
                for name, value in dict(step(release_job(), CUT_STEP)["env"]).items()
                if not str(value).startswith("${{")
            },
        },
        stub_bin=stub_bin,
    )
    notes = runner_temp / "notes.md"
    return (
        finished.returncode,
        notes.read_text(encoding="utf-8") if notes.exists() else "",
        calls.read_text(encoding="utf-8") if calls.exists() else "",
        finished.stdout + finished.stderr,
    )


def a_released_repository(repository: Path) -> None:
    commit(
        repository,
        "the previous release",
        {
            "config/policy.yaml": "rules\n",
            "config/capacity.yaml": "shapes\n",
            "src/edullm_platform/cli/main.py": "code\n",
        },
    )
    git(repository, "tag", "--annotate", "v0.2.1", "--message", "v0.2.1")
    git(repository, "push", "--quiet", "origin", "HEAD", "v0.2.1")


def test_the_tag_reaches_the_remote_and_the_release_is_cut_from_it(
    repository: Path, tmp_path: Path
) -> None:
    """What a green run leaves behind, read off the remote rather than off the log."""
    a_released_repository(repository)
    commit(repository, "a merge", {"src/edullm_platform/cli/main.py": "code\nmore\n"})

    code, notes, calls, said = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert code == 0, said
    assert "refs/tags/v0.2.2" in git(repository, "ls-remote", "--tags", "origin")
    assert "release create v0.2.2 --title v0.2.2" in calls
    assert "--generate-notes" in calls
    assert notes


def test_both_halves_of_the_note_describe_the_same_range(
    repository: Path, tmp_path: Path
) -> None:
    """THE TRAP ON THE FIRST CUT AFTER A RELEASE WAS MADE BY HAND. Mutation: drop the flag.

    ``--generate-notes`` builds its pull request list against the previous *release*, and
    the headline used to be computed against ``git describe``, which answers the newest tag
    reachable from here. Those were the same thing until ``v0.2.1`` was cut from a branch
    while this workflow was broken: it is ``releases/latest`` and it is not an ancestor of
    ``main``, so ``git describe`` on ``main`` answers ``v0.2.0``. A headline computed over
    the wider range would have claimed a configuration change that the list underneath it
    did not show.

    So the previous release is read once and handed to both.
    """
    a_released_repository(repository)
    commit(repository, "a merge", {"src/edullm_platform/cli/main.py": "code\nmore\n"})

    _, _, calls, said = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert "--notes-start-tag v0.2.1" in calls, said


def test_the_first_release_of_all_asks_for_no_starting_point(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: pass the flag with an empty value.

    ``gh release create --notes-start-tag ''`` is not "start from the beginning", it is a
    ref that does not resolve, and the first release this repository ever cuts is the one
    run nobody can retry by hand afterwards.
    """
    commit(repository, "everything so far", {"src/edullm_platform/cli/main.py": "code\n"})

    code, _, calls, said = run_cut(repository, tmp_path, tag="v0.1.0", latest="")

    assert code == 0, said
    assert "--notes-start-tag" not in calls


def test_the_notes_carry_the_install_line_the_code_spells(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: hand-write the install line into the notes.

    These notes are read by somebody deciding whether to re-install, so they are one of
    the places the command has to be right -- and the command was wrong for the whole life
    of the project in exactly this way, by being written out a second time somewhere no
    test looked. ``tests/test_cli_install_command.py`` holds the README, the guide and
    ``pyproject.toml`` to :func:`install_command`; a release note is the copy that file
    cannot see, because it does not exist until a release is cut.
    """
    a_released_repository(repository)
    commit(repository, "a merge", {"src/edullm_platform/cli/main.py": "code\nmore\n"})

    _, notes, _, _ = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert install_command(repository=PLATFORM_REPOSITORY, tag="v0.2.2") in notes
    # The other half of the same rule: naming the command uv answers wrongly is only
    # allowed beside uv's actual answer, which tests/test_cli_install_command.py enforces
    # over the tree and cannot enforce over a file generated at release time.
    assert "uv tool upgrade" in notes
    assert "Nothing to upgrade" in notes


def test_the_note_uses_a_full_stop_where_a_colon_would_introduce_no_list(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: put the colon back.

    The house standard keeps a colon only where it introduces a list, and this sentence
    introduces a clause. It is one character, and it is in the paragraph thirty-five people
    read on every release, which is the whole argument for a test rather than a proofread.
    """
    a_released_repository(repository)
    commit(repository, "a merge", {"src/edullm_platform/cli/main.py": "code\nmore\n"})

    _, notes, _, _ = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert "installed from git. It answers" in notes
    assert "installed from git:" not in notes


def test_a_release_that_moves_the_configuration_says_so_first(
    repository: Path, tmp_path: Path
) -> None:
    """The one thing generated notes cannot infer and the one thing a reader needs.

    A release that changed only code costs a researcher nothing to skip. One that moved
    the reviewed configuration is a release after which their ``edullm check`` and the
    platform answer the same question differently.
    """
    a_released_repository(repository)
    commit(repository, "a merge that moves the roster", {"config/policy.yaml": "stricter\n"})

    _, notes, _, _ = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert notes.startswith("**This release moves the reviewed configuration**")


def test_a_file_under_config_that_the_cli_never_opens_is_not_a_configuration_change(
    repository: Path, tmp_path: Path
) -> None:
    """WHAT v0.2.1 GOT WRONG, RUN. Mutation: diff ``config/`` instead of the six files.

    ``config/capacity.yaml`` is the only file that moved between ``v0.2.0`` and ``v0.2.1``,
    the CLI does not open it, and the note said the reviewed configuration had moved. Every
    researcher who re-installed on that sentence did so for nothing.
    """
    a_released_repository(repository)
    commit(repository, "a merge that moves the capacity table", {"config/capacity.yaml": "few\n"})

    _, notes, _, _ = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert notes.startswith("This release changes code only.")


def test_a_release_that_moves_only_code_says_that_instead(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: compare against the previous commit rather than the previous release.

    The two were the same thing while a release was cut on every qualifying merge. They
    are not now that the version moves in a pull request, because one release can carry
    several merges -- so a release whose *last* commit touched only code can still be one
    that moves the configuration. Two commits here, and only the first moves a file the CLI
    opens: against the previous commit this reads as code-only and is wrong.
    """
    a_released_repository(repository)
    commit(repository, "a merge that moves the roster", {"config/policy.yaml": "stricter\n"})
    commit(repository, "a merge that does not", {"src/edullm_platform/cli/main.py": "code\n2\n"})

    _, moved, _, _ = run_cut(repository, tmp_path, tag="v0.2.2", latest="v0.2.1")

    assert moved.startswith("**This release moves the reviewed configuration**")

    # The cut above left v0.2.2 behind, which is what the next release measures against.
    commit(repository, "another code merge", {"src/edullm_platform/cli/main.py": "code\n3\n"})

    _, code_only, _, _ = run_cut(repository, tmp_path, tag="v0.2.3", latest="v0.2.2")

    assert code_only.startswith("This release changes code only.")


def test_a_previous_release_whose_tag_is_gone_does_not_kill_the_run(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: diff against the name without checking that it resolves.

    A deleted tag leaves a release naming a ref nothing can reach, and ``git diff`` against
    it exits non-zero under ``set -e`` -- which loses the tag, the release and the notes
    rather than losing one sentence. It falls back to saying the configuration moved,
    because a reader told to re-check config that did not move has lost a minute and one not
    told about config that did has a check that disagrees with admission.
    """
    a_released_repository(repository)
    commit(repository, "a merge", {"src/edullm_platform/cli/main.py": "code\nmore\n"})

    code, notes, _, said = run_cut(repository, tmp_path, tag="v0.2.2", latest="v9.9.9")

    assert code == 0, said
    assert notes.startswith("**This release moves the reviewed configuration**")


def run_guard(
    repository: Path, tmp_path: Path, *, latest: str
) -> tuple[int, str]:
    stub_bin = tmp_path / "bin"
    uv_running(stub_bin)
    write_stub(stub_bin, "gh", f'if [ -n "{latest}" ]; then echo "{latest}"; else exit 1; fi\n')

    finished = run_step_script(
        script_of(checks_job(), GUARD_STEP),
        cwd=repository,
        env={"HOME": str(tmp_path), "GH_TOKEN": "not-a-token", "GH_REPO": PLATFORM_REPOSITORY},
        stub_bin=stub_bin,
    )
    return finished.returncode, finished.stdout + finished.stderr


def test_a_change_a_release_must_carry_is_refused_without_a_version_for_it(
    repository: Path, tmp_path: Path
) -> None:
    """THE HALF THAT HAS TO HAPPEN BEFORE THE MERGE. Mutation: delete this step.

    Without it the bump depends on somebody remembering, on a repository where the reviewed
    configuration moves most weeks -- and the merge that forgets is a red
    ``release-tag.yml`` and a change that reaches nobody until a human notices. The
    workflow's own header used to reject this idea as something "forgotten in exactly the
    hotfix where the config change is most urgent". That was an argument against
    discipline; this is a required check inside a required job, and forgetting it is not
    one of the things that can happen to it.
    """
    install_real_tooling(repository, version="0.2.1")
    commit(repository, "a change to the reviewed configuration", {"config/policy.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 1, said
    assert "::error::" in said
    assert BUMP_COMMAND in said
    assert "config/policy.yaml" in said, "say which path made this a release"


def test_the_same_change_with_a_patch_of_its_own_passes(
    repository: Path, tmp_path: Path
) -> None:
    install_real_tooling(repository, version="0.2.2")
    commit(repository, "a change to the reviewed configuration", {"config/policy.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 0, said
    assert "v0.2.2" in said
    assert "patch" in said


def test_a_minor_is_a_thing_the_check_accepts(repository: Path, tmp_path: Path) -> None:
    """WHAT #199 COULD NOT HAVE. Mutation: accept only the next patch.

    That change added a refusal which stops a submission that used to go through, which the
    house standard calls a minor in as many words. Nothing in the system could produce one:
    the workflow computed the next patch and pushed it, so a hand-written ``0.3.0`` was a
    number a bot would walk over. It shipped inside ``v0.2.0`` with sixty other merges.
    """
    install_real_tooling(repository, version="0.3.0")
    commit(repository, "a refusal that can stop a submission", {"config/policy.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 0, said
    assert "v0.3.0" in said
    assert "minor" in said


def test_a_major_is_too(repository: Path, tmp_path: Path) -> None:
    install_real_tooling(repository, version="1.0.0")
    commit(repository, "a flag that means something else now", {"config/policy.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 0, said
    assert "major" in said


def test_a_version_that_is_no_step_at_all_is_refused(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: accept anything higher than the latest release.

    ``0.2.9`` from ``v0.2.1`` is a typo, not a statement, and the tag it produces is one
    nobody can explain against the seven releases that do not exist between them. The check
    knows exactly three answers and says all three when it refuses.
    """
    install_real_tooling(repository, version="0.2.9")
    commit(repository, "a change to the reviewed configuration", {"config/policy.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 1, said
    for size in ("patch", "minor", "major"):
        assert f"{BUMP_COMMAND} {size}" in said


def test_a_change_no_installed_cli_would_notice_is_asked_for_nothing(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: require a bump from every pull request.

    ``main``'s ordinary state is that its declared version is already released -- that is
    what cutting a release from it means -- so a rule that did not read the diff would
    make every documentation fix carry a version bump, and a rule everybody has to satisfy
    for no reason is one that gets a blanket exemption within the week.
    """
    install_real_tooling(repository, version="0.2.1")
    commit(repository, "a documentation fix", {"README.md": "words\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 0, said
    assert "needs no version of its own" in said


def test_a_change_to_a_module_the_cli_never_imports_is_asked_for_nothing(
    repository: Path, tmp_path: Path
) -> None:
    """THE SIX MERGES THAT CRIED WOLF, RUN AGAINST THE REAL TRIGGER LIST.

    Mutation: widen the trigger back to ``src/edullm_platform/**``.

    ``run_comparison.py`` compares two finished runs from a maintainer's terminal and no
    installed ``edullm`` imports it. Under the old list #202 and #206 each moved
    ``releases/latest`` and fired the staleness warning on thirty-five machines for it.
    """
    install_real_tooling(repository, version="0.2.1")
    commit(
        repository,
        "a change to a module nothing installed imports",
        {"src/edullm_platform/run_comparison.py": "code\n"},
    )

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 0, said
    assert "needs no version of its own" in said


def test_a_pull_request_whose_base_it_cannot_find_is_not_blocked(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: let the diff fail the step.

    A checkout with no parent is not evidence that anything is wrong, and blocking every
    merge in the repository on a step that has decided it is confused costs more than the
    thing it protects -- which ``release-tag.yml`` catches anyway, loudly, one merge later.
    Failing open is the right way round for this check and the wrong way round for that
    one, which is why they are two checks.
    """
    commit(repository, "the only commit there is", {"config/policy.yaml": "rules\n"})

    code, said = run_guard(repository, tmp_path, latest="v0.2.1")

    assert code == 0, said
    assert "no base commit" in said


def test_a_repository_with_no_release_yet_is_not_second_guessed(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: treat a missing release as a refusal.

    There is nothing to be a step above, and the first release of all is exactly the moment
    somebody cannot afford a check that has decided it knows better.
    """
    install_real_tooling(repository, version="0.1.0")
    commit(repository, "the first change of all", {"config/policy.yaml": "rules\n"})

    code, said = run_guard(repository, tmp_path, latest="")

    assert code == 0, said
    assert "No release exists yet" in said
