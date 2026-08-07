"""The last way past the major ceiling, and the only one that runs after the fact.

WHY THIS WORKFLOW EXISTS AT ALL. ``tools/next_version.py`` refuses to compute a version
above ``MAJOR_CEILING`` and refuses to report one it finds declared, and ``ci.yml`` asks it
that on every pull request. Neither of them is between anybody and
``git push origin v5.0.0``. This repository has no rulesets and no tag protection --
``release-tag.yml`` says so in its own comments, and both endpoints answer empty -- so the
push succeeds and, without this, succeeds silently.

WHAT IS TESTED HERE AND WHAT IS NOT. The script is executed rather than described, against
the real ``tools/next_version.py``, because the number it compares against has to come from
the constant rather than from a copy in YAML. What is not tested is the push itself, which
is GitHub's to do; the trigger is asserted instead.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from workflow_support import (
    PROJECT_ROOT,
    WORKFLOWS_ROOT,
    load_workflow,
    only_job,
    run_step_script,
    step,
    write_stub,
)

from tools.next_version import MAJOR_CEILING

WORKFLOW_PATH = WORKFLOWS_ROOT / "refuse-a-tag-above-the-ceiling.yml"
COMPARE_STEP = "Compare the tag against MAJOR_CEILING"


def workflow() -> dict[str, object]:
    return load_workflow(WORKFLOW_PATH)


def run_compare(tmp_path: Path, tag: str) -> tuple[int, str, str]:
    """The step's own script, run against a copy of this repository's real tool.

    Copied into a working directory rather than run in the project root, for two reasons
    that are both about not writing where the checkout is: ``run_step_script`` leaves the
    script it executes beside the code, and this suite runs in the tree somebody is working
    in. The tool itself is the real one, because the whole claim being tested is that the
    number comes from ``MAJOR_CEILING`` and not from a copy in the YAML.

    ``python3`` is stubbed to this suite's interpreter. The tool imports ``tomllib``, which
    the system ``python3`` on a developer machine predates, and the runner's does not --
    ``release-tag.yml`` has called it by that name since it was written.
    """
    work = tmp_path / "work"
    (work / "tools").mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "tools" / "next_version.py", work / "tools")
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "python3", f'exec "{sys.executable}" "$@"\n')
    summary = tmp_path / "step-summary"
    summary.write_text("", encoding="utf-8")
    finished: subprocess.CompletedProcess[str] = run_step_script(
        str(step(only_job(workflow()), COMPARE_STEP)["run"]),
        cwd=work,
        env={
            "HOME": str(tmp_path),
            "TAG": tag,
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        stub_bin=stub_bin,
    )
    return (
        finished.returncode,
        summary.read_text(encoding="utf-8"),
        finished.stdout + finished.stderr,
    )


def test_it_runs_on_a_pushed_tag_and_on_nothing_else() -> None:
    """Mutation: trigger on a push to main.

    A tag that arrives by hand is the only event this is about, and it is an event nothing
    else in this repository watches. Triggering on anything wider would run a job that has
    no tag to read and would answer about ``pyproject.toml`` instead, which two other places
    already do better.
    """
    triggers = workflow()["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"push"}
    assert triggers["push"] == {"tags": ["v*"]}


def test_it_writes_nothing_anywhere() -> None:
    """Mutation: give it ``contents: write`` so it can delete the tag.

    Deleting refs from a workflow on a repository where three agents destroyed uncommitted
    work in twelve hours is a worse failure than the stray tag it would clean up. This job
    reads a ref name and a constant and decides whether to go red, and the token it holds
    should not be able to do more than that.
    """
    assert workflow()["permissions"] == {"contents": "read"}


def test_a_tag_on_the_ceiling_passes(tmp_path: Path) -> None:
    """The ordinary case, which is every release this repository cuts.

    Mutation: refuse the ceiling's own major rather than the ones above it. That would go
    red on all forty-seven existing tags and on every release from here, which is the shape
    of guard that gets deleted in a week.
    """
    code, summary, said = run_compare(tmp_path, f"v{MAJOR_CEILING}.9.9")

    assert code == 0, said
    assert summary == ""


@pytest.mark.parametrize("above", [1, 8])
def test_a_tag_above_the_ceiling_goes_red_and_says_where_the_ceiling_is(
    tmp_path: Path, above: int
) -> None:
    """THE CASE THIS FILE EXISTS FOR. Mutation: compare only against one specific major.

    Whoever reads the summary is either somebody who mistyped a tag or somebody who meant
    it. The first needs the two commands that take it back; the second needs to know the
    ceiling is a declared constant and that lifting it is a reviewed commit rather than an
    argument to something.
    """
    tag = f"v{MAJOR_CEILING + above}.0.0"

    code, summary, said = run_compare(tmp_path, tag)

    assert code == 1
    assert "::error::" in said
    assert tag in summary
    assert "MAJOR_CEILING" in summary
    assert "tools/next_version.py" in summary
    assert f"git push --delete origin {tag}" in summary


def test_the_ceiling_it_compares_against_is_the_declared_one(tmp_path: Path) -> None:
    """Mutation: write the number into the YAML.

    A workflow cannot import anything, so the temptation is a literal -- and the literal is
    wrong on exactly one day, the day somebody lifts the ceiling, which is the day this job
    must not go red on a release the rest of the repository has agreed to. It asks the tool.
    """
    _, summary, _ = run_compare(tmp_path, f"v{MAJOR_CEILING + 1}.0.0")

    assert f"currently {MAJOR_CEILING}" in summary
    assert str(MAJOR_CEILING) not in WORKFLOW_PATH.read_text(encoding="utf-8"), (
        "the ceiling is readable from tools/next_version.py --ceiling and a second copy "
        "here is the one that goes stale"
    )


def test_a_tag_that_is_not_a_version_is_not_this_job_s_business(tmp_path: Path) -> None:
    """Mutation: treat an unparseable tag as above the ceiling.

    ``release-tag.yml`` cuts exactly ``v<major>.<minor>.<patch>`` and nothing else here cuts
    tags at all, so a ``v``-prefixed name that is not three integers is somebody using the
    namespace for something this has no opinion about. Refusing it would be a red run with
    no remedy in it.
    """
    code, summary, said = run_compare(tmp_path, "vsomething-else")

    assert code == 0, said
    assert summary == ""
