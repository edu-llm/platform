"""The reading of a merge queue that a person allocates versions from.

WHY THIS IS TESTED WHEN IT ONLY PRINTS. ``tools/version_ledger.py`` exists because three
separate readings of the same queue were wrong in one evening, and the remedy for that cannot
be a fourth reading nobody checks. What it prints is acted on twenty times in sequence by
somebody who has stopped verifying by then, so a wrong number here is worse than no tool: it
is the same mistake with an authority the summaries did not have.

The functions below are the whole of its judgement and none of them touch the network. The
report is assembled from dictionaries shaped like ``gh``'s answer, so the cases that have
actually happened on this repository are the cases that are written down.
"""

from __future__ import annotations

from pathlib import Path

from tools.release_paths import RELEASE_WORKFLOW, release_paths
from tools.version_ledger import (
    claimed_size,
    declares,
    earns_a_release,
    ledger,
    merge_order,
    only_its_own_bump,
)

PATHS = release_paths(RELEASE_WORKFLOW.read_text(encoding="utf-8"))

#: What ``tools/next_version.py --bump patch`` leaves in ``pyproject.toml`` and nothing else.
#: This is #427: a report file that is not a release path, plus a bump that made the pull
#: request look like one because ``pyproject.toml`` is.
ONLY_A_BUMP = """@@
-# WHY THIS IS A MINOR RATHER THAN A PATCH. the verdict reads the image
-version = "4.10.0"
+version = "4.10.1"
-#   uv tool install --force git+https://github.com/edu-llm/platform@v4.10.0
+#   uv tool install --force git+https://github.com/edu-llm/platform@v4.10.1
"""

#: A bump carrying a minor's sentence, taken off a base far below the current tip. This is
#: #428 and #429: the subtraction says nothing, and the comment says everything.
A_MINOR_OFF_A_STALE_BASE = """@@
-# WHY THIS IS A MINOR RATHER THAN A PATCH. studio opens the browser itself
-version = "4.5.0"
+# WHY THIS IS A MINOR RATHER THAN A PATCH. a command whose shell would report an exit
+version = "4.11.0"
-#   uv tool install --force git+https://github.com/edu-llm/platform@v4.5.0
+#   uv tool install --force git+https://github.com/edu-llm/platform@v4.11.0
"""


def _pull(number: int, *, base: str, head: str, files: dict[str, str | None]) -> dict[str, object]:
    return {
        "number": number,
        "baseRefName": base,
        "headRefName": head,
        "isDraft": False,
        "files": [{"filename": name, "patch": patch} for name, patch in files.items()],
    }


def test_a_bump_that_is_its_own_only_release_path_is_recognised() -> None:
    assert only_its_own_bump(ONLY_A_BUMP) is True
    assert only_its_own_bump(A_MINOR_OFF_A_STALE_BASE) is True


def test_a_real_change_to_pyproject_is_not_mistaken_for_a_bump() -> None:
    """A dependency moving is a change to a release path and earns its number."""
    assert only_its_own_bump(ONLY_A_BUMP + '+    "boto3>=1.40",\n') is False
    assert only_its_own_bump(None) is False
    assert only_its_own_bump("@@\n") is False


def test_the_declared_step_is_read_off_both_sides() -> None:
    assert declares(ONLY_A_BUMP) == ("4.10.0", "4.10.1")
    assert declares(None) == (None, None)


def test_the_authors_sentence_beats_the_arithmetic() -> None:
    """4.5.0 to 4.11.0 is not one step of any size, and it is still a minor.

    The case that made this function read a comment at all. Every branch in a queue of this
    depth was cut off a stale base, so subtracting the two versions answers for none of them
    and would quietly demote a minor to a patch.
    """
    assert claimed_size(A_MINOR_OFF_A_STALE_BASE, "4.5.0", "4.11.0") == "minor"


def test_a_bump_with_no_sentence_is_a_patch() -> None:
    assert claimed_size(ONLY_A_BUMP, "4.10.0", "4.10.1") == "patch"
    assert claimed_size(None, None, None) == "patch"


def test_a_stacked_pull_request_is_ordered_behind_the_one_it_sits_on() -> None:
    pulls = {
        428: _pull(428, base="main", head="the-run-that-lied", files={}),
        421: _pull(421, base="logs", head="the-verdict", files={}),
        420: _pull(420, base="main", head="logs", files={}),
    }
    assert merge_order(pulls, None) == [420, 421, 428]
    assert merge_order(pulls, [428, 420, 421]) == [428, 420, 421]


def test_tools_is_not_a_release_path_so_this_needs_no_version() -> None:
    """The claim this tool prints about itself, held to the list rather than restated."""
    assert earns_a_release(["tools/version_ledger.py"], PATHS) == []
    assert earns_a_release(["src/edullm_platform/submission.py"], PATHS) != []
    assert earns_a_release(["src/edullm_platform/cli/preflight.py"], PATHS) != []


def test_a_gratuitous_bump_takes_no_number_and_the_chain_closes_over_it() -> None:
    """#424, #427 and #428 as they actually stood, which is the whole reason for the tool.

    #427 declared 4.10.1 against #424's 4.10.1. Dropping the bump it did not earn is what
    makes that a non-collision rather than a tie somebody has to break.
    """
    pulls = {
        424: _pull(
            424,
            base="the-verdict",
            head="a-timeout",
            files={
                "src/edullm_platform/execution.py": "@@\n+#: a comment\n",
                "pyproject.toml": '@@\n-version = "4.10.0"\n+version = "4.10.1"\n',
            },
        ),
        427: _pull(
            427,
            base="a-timeout",
            head="alt-cl",
            files={
                "config/reports/resume-demonstrations.yaml": "@@\n+- run: x\n",
                "pyproject.toml": ONLY_A_BUMP,
            },
        ),
        428: _pull(
            428,
            base="alt-cl",
            head="the-run-that-lied",
            files={
                "src/edullm_platform/submission.py": "@@\n+refuse()\n",
                "pyproject.toml": A_MINOR_OFF_A_STALE_BASE,
            },
        ),
    }
    printed = "\n".join(ledger(pulls, [424, 427, 428], "4.10.0", PATHS))
    assert "#424  4.10.1    4.10.1    ok" in printed
    assert "DROP the bump" in printed
    # 4.11.0 and not 4.12.0: the number #427 gave back is not consumed by anybody.
    assert "#428  4.11.0    4.11.0    ok" in printed


def test_the_squash_hazard_is_named_for_every_pull_request_with_a_child() -> None:
    pulls = {
        420: _pull(420, base="main", head="logs", files={}),
        421: _pull(421, base="logs", head="the-verdict", files={}),
    }
    printed = "\n".join(ledger(pulls, [420, 421], "4.9.0", PATHS))
    assert "MERGE COMMIT, NOT SQUASH, for #420." in printed
    assert "#421" not in printed.split("MERGE COMMIT")[1].split("\n")[0]


def test_the_report_is_lines_and_not_a_print() -> None:
    """Pure, so that the thing a maintainer acts on is the thing a test read."""
    assert all(isinstance(line, str) for line in ledger({}, [], "4.10.0", PATHS))


def test_the_workflow_it_reads_is_where_it_thinks_it_is() -> None:
    assert RELEASE_WORKFLOW.is_file()
    assert Path("tools/version_ledger.py").is_file()
