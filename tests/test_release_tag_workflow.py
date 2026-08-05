"""The workflow that cuts releases, and the check that makes it possible for it to.

THIS FILE EXISTS BECAUSE THAT WORKFLOW FAILED ON EVERY MERGE FOR FIVE MERGES AND NOTHING
NOTICED. It computed the next patch version, committed the bump to ``pyproject.toml`` and
``uv.lock``, and pushed that commit to ``main``; branch protection refuses a push to
``main``, so every run died on the same line:

    remote: - Changes must be made through a pull request.
    remote: - 2 of 2 required status checks are expected.

``v0.2.0`` went on being the answer ``repos/edu-llm/platform/releases/latest`` gave while
five merges of CLI and configuration work sat unreleased behind it. The install line
researchers are handed pins a tag, so the automation written to keep that line current was
the thing keeping it stale -- and the CLI's own staleness probe, comparing an install
against that same endpoint, told everybody they were current the whole time. There was no
test on the file at all, which is why reading it was the only way to find out.

So the two properties below are the ones worth holding. **Nothing here may write to a
branch**: the tag and the release are the only writes, neither ref is protected, and the
protection on ``main`` is not weakened, bypassed or exempted to make this work. And **the
version has to be declared before the merge rather than after it**, because it is a
literal in a file and only a pull request may put a commit on ``main`` -- which is what
``ci.yml``'s last step is for, and why it is tested here beside the workflow it serves
rather than in ``tests/test_ci_workflow.py`` with the rest of that file's arrangement.

The behavioural cases run the ``run:`` bodies as the runner runs them, against real git
repositories with a real remote. A workflow asserted only by reading its YAML is how the
push to ``main`` survived review in the first place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from workflow_support import (
    WORKFLOWS_ROOT,
    load_workflow,
    only_job,
    run_step_script,
    shell_syntax_without_heredoc_bodies,
    step,
    unreal_context_references,
    write_stub,
)

from edullm_platform.cli.actions import PLATFORM_REPOSITORY
from edullm_platform.cli.release import install_command

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
    """THE CASE THIS FILE IS FOR. Mutation: put the bump commit back.

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
    creates = [command for command in every_release_command() if command.startswith("gh release")]

    assert len(creates) == 1
    assert creates[0].startswith("gh release create")


def test_it_can_be_started_by_hand_as_well_as_by_a_merge() -> None:
    """The handle it did not have on the morning five releases were missing.

    With only the push trigger, cutting a release the automation missed means a commit to
    ``main`` whose only purpose is to touch one of the paths below -- which is a pull
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


def test_the_two_workflows_agree_on_what_a_release_has_to_carry() -> None:
    """Mutation: add a path to one list.

    ``release-tag.yml`` triggers on these paths and ``ci.yml`` requires a version for a
    change touching them, so the two lists are one rule written twice -- Actions has no
    way to share them. Drifted apart in the direction that matters, a change qualifies for
    a release without ever being asked to declare a version for it, and the merge that
    should have cut the release goes red instead.
    """
    triggering = load_workflow(WORKFLOW_PATH)["on"]["push"]["paths"]
    guarded = str(step(checks_job(), GUARD_STEP)["env"]["RELEASE_PATHS"]).split()

    assert {path.removesuffix("/**") for path in triggering} == set(guarded)
    assert len(guarded) == len(set(guarded)) == len(triggering)


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
    # Failing it means adding one line to the pull request. Somebody who has to do that
    # would rather learn it alongside the test results than instead of them, and the step
    # is the cheapest in the job so its position costs nothing.
    steps = checks_job()["steps"]
    assert isinstance(steps, list)

    assert steps[-1]["name"] == GUARD_STEP


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

    A bare repository rather than a fake remote: ``git push origin <tag>`` and ``git
    ls-remote --tags origin`` are the two operations under test on the network side, and
    both are exactly themselves against a path.
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
    commit(repository, "a merge", {"src/edullm_platform/cli.py": "code\n"})

    code, output, summary, _ = run_decide(repository, tmp_path, declared="0.2.1")

    assert code == 0
    assert "version=0.2.1" in output
    assert "tag=v0.2.1" in output
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
    commit(repository, "a merge", {"config/capacity.yaml": "shapes\n"})
    git(repository, "tag", "--annotate", "v0.2.1", "--message", "v0.2.1")

    code, output, summary, said = run_decide(repository, tmp_path, declared="0.2.1")

    assert code == 1
    assert output == "", "a failed decision must not leave a version for the next step"
    assert "::error::" in said
    assert BUMP_COMMAND in summary
    assert "v0.2.1" in summary
    assert "cannot write to `main`" in summary


def run_cut(repository: Path, tmp_path: Path, *, tag: str) -> tuple[int, str, str, str]:
    stub_bin = tmp_path / "bin"
    calls = tmp_path / "gh-calls"
    write_stub(stub_bin, "gh", f'printf "%s\\n" "$*" >>"{calls}"\n')
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
        {"config/capacity.yaml": "shapes\n", "src/edullm_platform/cli.py": "code\n"},
    )
    git(repository, "tag", "--annotate", "v0.2.0", "--message", "v0.2.0")
    git(repository, "push", "--quiet", "origin", "HEAD", "v0.2.0")


def test_the_tag_reaches_the_remote_and_the_release_is_cut_from_it(
    repository: Path, tmp_path: Path
) -> None:
    """What a green run leaves behind, read off the remote rather than off the log."""
    a_released_repository(repository)
    commit(repository, "a merge", {"src/edullm_platform/cli.py": "code\nmore\n"})

    code, notes, calls, said = run_cut(repository, tmp_path, tag="v0.2.1")

    assert code == 0, said
    assert "refs/tags/v0.2.1" in git(repository, "ls-remote", "--tags", "origin")
    assert calls.splitlines()[0].startswith("release create v0.2.1 --title v0.2.1")
    assert "--generate-notes" in calls
    assert notes


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
    commit(repository, "a merge", {"src/edullm_platform/cli.py": "code\nmore\n"})

    _, notes, _, _ = run_cut(repository, tmp_path, tag="v0.2.1")

    assert install_command(repository=PLATFORM_REPOSITORY, tag="v0.2.1") in notes
    # The other half of the same rule: naming the command uv answers wrongly is only
    # allowed beside uv's actual answer, which tests/test_cli_install_command.py enforces
    # over the tree and cannot enforce over a file generated at release time.
    assert "uv tool upgrade" in notes
    assert "Nothing to upgrade" in notes


def test_a_release_that_moves_the_configuration_says_so_first(
    repository: Path, tmp_path: Path
) -> None:
    """The one thing generated notes cannot infer and the one thing a reader needs.

    A release that changed only code costs a researcher nothing to skip. One that moved
    the reviewed configuration is a release after which their ``edullm check`` and the
    platform answer the same question differently, and ``config/`` moved twice within
    hours of the CLI merging in the last month.
    """
    a_released_repository(repository)
    commit(repository, "a merge that moves the shapes", {"config/capacity.yaml": "fewer\n"})

    _, notes, _, _ = run_cut(repository, tmp_path, tag="v0.2.1")

    assert notes.startswith("**This release moves the reviewed configuration**")


def test_a_release_that_moves_only_code_says_that_instead(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: compare against the previous commit rather than the previous tag.

    The two were the same thing while a release was cut on every qualifying merge. They
    are not now that the version moves in a pull request, because one release can carry
    several merges -- so a release whose *last* commit touched only code can still be one
    that moves the configuration. Two commits here, and only the first moves ``config/``:
    against the previous commit this reads as code-only and is wrong.
    """
    a_released_repository(repository)
    commit(repository, "a merge that moves the shapes", {"config/capacity.yaml": "fewer\n"})
    commit(repository, "a merge that does not", {"src/edullm_platform/cli.py": "code\nmore\n"})

    _, moved, _, _ = run_cut(repository, tmp_path, tag="v0.2.1")

    assert moved.startswith("**This release moves the reviewed configuration**")

    commit(repository, "another code merge", {"src/edullm_platform/cli.py": "code\nmore\nyet\n"})

    _, code_only, _, _ = run_cut(repository, tmp_path, tag="v0.2.2")

    assert code_only.startswith("This release changes code only.")


def run_guard(repository: Path, tmp_path: Path, *, declared: str) -> tuple[int, str]:
    stub_bin = tmp_path / "bin"
    python3_saying(stub_bin, declared)
    guard = step(checks_job(), GUARD_STEP)
    environment = {str(name): str(value) for name, value in dict(guard["env"]).items()}

    finished = run_step_script(
        str(guard["run"]),
        cwd=repository,
        env={"HOME": str(tmp_path), **environment},
        stub_bin=stub_bin,
    )
    return finished.returncode, finished.stdout + finished.stderr


def test_a_change_a_release_must_carry_is_refused_without_a_version_for_it(
    repository: Path, tmp_path: Path
) -> None:
    """THE HALF THAT HAS TO HAPPEN BEFORE THE MERGE. Mutation: delete this step.

    Without it the bump depends on somebody remembering, on a repository where ``config/``
    took 55 commits in thirty days -- and the merge that forgets is a red
    ``release-tag.yml`` and a change that reaches nobody until a human notices. The
    workflow's own header used to reject this idea as something "forgotten in exactly the
    hotfix where the config change is most urgent". That was an argument against
    discipline; this is a required check inside a required job, and forgetting it is not
    one of the things that can happen to it.
    """
    a_released_repository(repository)
    commit(repository, "a change to the reviewed configuration", {"config/capacity.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, declared="0.2.0")

    assert code == 1
    assert "::error::" in said
    assert BUMP_COMMAND in said
    assert "config/capacity.yaml" in said, "say which path made this a release"


def test_the_same_change_with_a_version_of_its_own_passes(
    repository: Path, tmp_path: Path
) -> None:
    a_released_repository(repository)
    commit(repository, "a change to the reviewed configuration", {"config/capacity.yaml": "few\n"})

    code, said = run_guard(repository, tmp_path, declared="0.2.1")

    assert code == 0
    assert "v0.2.1" in said


def test_a_change_no_installed_cli_would_notice_is_asked_for_nothing(
    repository: Path, tmp_path: Path
) -> None:
    """Mutation: require a bump from every pull request.

    ``main``'s ordinary state is that its declared version is already released -- that is
    what cutting a release from it means -- so a rule that did not read the diff would
    make every documentation fix carry a version bump, and a rule everybody has to satisfy
    for no reason is one that gets a blanket exemption within the week.
    """
    a_released_repository(repository)
    commit(repository, "a documentation fix", {"README.md": "words\n"})

    code, said = run_guard(repository, tmp_path, declared="0.2.0")

    assert code == 0
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
    commit(repository, "the only commit there is", {"config/capacity.yaml": "shapes\n"})

    code, said = run_guard(repository, tmp_path, declared="0.2.0")

    assert code == 0
    assert "no base commit" in said
