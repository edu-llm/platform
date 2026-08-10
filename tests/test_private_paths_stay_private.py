"""No tracked file names a path that only exists on somebody's laptop.

**WHY A TEST AND NOT A RULE IN A DOCUMENT, WHICH IS WHAT THIS ALREADY WAS.** ``docs-frank/``
and ``.superpowers/`` are local-only working trees. They are gitignored, so nothing under them
has ever been committed, and that half of the rule enforces itself. The half that does not is
the *reference*: a comment in a tracked file saying "see ``docs-frank/reference/<a document>``"
publishes the shape of a private tree to everybody reading a public repository and sends them
somewhere they cannot go.

That is not a hypothetical and the scale is the argument. On 2026-08-10 a sweep of this
repository found eighty-four such citations across sixty tracked files on ``main``, every one
of them written by somebody who knew the rule, none of them caught by anything. A rule that
lives only in prose decays exactly like this: silently, in the direction of more, and nobody
is at fault for any single line.

**ONE OF THE EIGHTY-FOUR WAS IN A TEST'S OWN FAILURE MESSAGE, TELLING THE NEXT AUTHOR TO
WRITE ONE.** ``test_cli_no_hardcoded_bounds.py`` refused a hardcoded count and offered, as the
remedy, citing the private overview by path. So the leak was not only uncaught, it was being
taught. That is the specific thing a test fixes and a paragraph cannot.

**IT ALSO CATCHES A HOME DIRECTORY, WHICH IS THE SAME BUG WEARING DIFFERENT CLOTHES AND COSTS
MORE.** ``tests/test_checkpoint_shape_agreement.py`` located a sibling checkout at an absolute
path under one person's home directory. It leaked who wrote it, and it made both checks in
that file skip on every machine except that one -- while printing the same sentence they print
when the checkout is genuinely absent, so the skip read as "you do not have OLMo-core" rather
than "this test cannot run here". A check that can only pass on one laptop is a check nobody
has.

**ASKED OF GIT RATHER THAN OF THE FILESYSTEM**, for the reason
``test_cli_install_command.py::readable_files`` writes out at length: a walk reads whatever a
working directory happens to hold, which on this repository means the private trees themselves.
``git ls-files`` is the same set on every machine and in CI, and it is exactly what a push
carries -- which is the thing being policed. Nothing here ever opens a file under a private
path, because no such file is tracked, and :func:`test_no_private_file_is_tracked_at_all` is
what keeps that true.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The local-only trees. Named as prefixes rather than as whole paths because the leak is a
#: mention of any depth: ``docs-frank`` alone still says the tree exists and still dangles.
PRIVATE_TREES = ("docs-frank", ".superpowers")

#: An absolute path into somebody's home directory, in the three spellings this repository
#: could plausibly grow one. The trailing segment is required so that prose about ``/Users``
#: in the abstract is not a finding.
#:
#: ``/home/runner/`` is excluded because it is not somebody's home. It is the home directory
#: every GitHub-hosted runner has, it is identical on all of them, and it appears verbatim in
#: the Actions output this repository asserts against -- so it names nobody and cannot make a
#: check machine-specific, which are the two things this rule is for.
HOME_DIRECTORY = re.compile(
    r"(?:/Users/|/home/(?!runner/)|[A-Za-z]:[\\/]Users[\\/])[A-Za-z][\w.\- ]*[\\/]",
)

#: A mention of a private tree, in the two spellings a path is written in Python and prose.
PRIVATE_TREE = re.compile(r"(?<![\w./-])(?:docs-frank|\.superpowers)(?![\w-])")

#: The evidence capture tools refuse to write outside this. It is a real local directory and
#: they have to be able to name it.
EVIDENCE_ROOT = re.compile(r"docs-frank/working/[a-z0-9-]*evidence\b|docs-frank/working/")

#: ``git ls-files`` reports these and there is nothing textual in them to police. Kept short
#: on purpose: a suffix added here is a suffix the rule stops covering, and the JSON schemas
#: are the reason nothing broad is here at all -- the first sweep found a private path inside
#: ``schemas/result-manifest.schema.json``, which arrived there because it is generated from
#: a docstring and nobody thinks of a generated file as prose.
BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".whl"})

#: **THE TWO FILES THAT MUST NAME A PRIVATE TREE, BECAUSE NAMING IT IS WHAT KEEPS IT PRIVATE.**
#: Deleting the two lines in the gitignore is what would commit the trees, and a rule cannot
#: forbid a string it is not allowed to write down.
#:
#: This file policing itself would be worth arguing for if the hole were a plausible one, and
#: it is not: everything here is about this one rule, it is read by anybody changing the rule,
#: and a citation of a private document parked in it would be the only sentence in the file
#: not doing that job. The gitignore is the same argument in two lines.
NAMES_THEM_IN_ORDER_TO_FORBID_THEM = frozenset(
    {
        ".gitignore",
        "tests/test_private_paths_stay_private.py",
    }
)

#: **THE WRITE FENCE, WHICH POINTS AT THE PRIVATE TREE IN ORDER TO PROTECT THE REPOSITORY AND
#: IS THEREFORE THE ONE EXEMPTION WORTH HAVING.** Every capture tool here reads a live AWS
#: account and refuses any ``--output-dir`` outside ``docs-frank/working/<phase>-evidence``,
#: so a capture full of account ids lands somewhere gitignored and cannot be committed by
#: somebody who was not paying attention. The string is the mechanism. Removing it would not
#: make anything more private; it would either break the fence or rename the destination to a
#: second private directory with the same problem and no history.
#:
#: These files are exempt from naming a private tree and are NOT exempt from the rule's point:
#: :func:`test_the_write_fence_names_nothing_but_the_evidence_root` holds them to naming the
#: evidence root and nothing else, so a citation of a private *document* landing in one of
#: them still fails.
THE_WRITE_FENCE = frozenset(
    {
        "config/organization.yaml",
        "infra/README.md",
        "tests/test_capture_phase1_evidence_cli.py",
        "tests/test_capture_phase1_run_evidence.py",
        "tests/test_capture_tooling.py",
        "tests/test_evidence.py",
        "tools/capture_phase0_evidence.py",
        "tools/capture_phase1_evidence.py",
        "tools/capture_phase2_evidence.py",
        "tools/capture_phase3_evidence.py",
        "tools/capture_phase4_evidence.py",
        "tools/capture_spine_evidence.py",
        "tools/probe_ec2_authorization.py",
        "tools/report_who_can_open_the_lead_gate.py",
    }
)

#: Invented home directories in fixtures that exercise path resolution, which are not anybody's
#: machine and cannot leak one. ``C:/Users/amy`` is what the Windows ``APPDATA`` case resolves
#: against, ``/Users/jane doe`` carries the space that the shell-quoting case exists for, and
#: ``/home/x/.aws/config`` is a profile path a refusal quotes back. All three are arguments
#: passed in by the test rather than defaults anything reads, so none of them can send a
#: reader or a run to a directory that exists, and all three would lose the property they test
#: if they were made relative.
INVENTED_HOME_DIRECTORIES = frozenset(
    {
        "tests/test_cli_release.py",
        "tests/test_lane_session.py",
        "tests/test_setup_script.py",
    }
)

THE_RULE = """
`docs-frank/` and `.superpowers/` are local-only working trees on one machine, and this is a
public repository. Committing a path into one of them publishes the private tree's shape to
anybody reading the repo and points them somewhere they cannot follow.

Reword rather than delete. These references exist because a comment was pointing at real
reasoning, so say what was concluded and why, inline, in the sentence that was going to cite
it. "The split is settled: X and Y are one pipeline and two publications" carries everything
"see decisions.md for the split" carried, and carries it to a reader who is not you.

An absolute path under a home directory is the same defect: it names whoever wrote it, and it
makes whatever reads it work on one laptop and quietly skip everywhere else. Derive it -- a
sibling of the repository root, or a flag.
"""


def tracked_text_files() -> list[tuple[str, str]]:
    """Every tracked file with text in it, as (path relative to the root, contents).

    A tracked path deleted in the working tree is skipped rather than read, and a file git
    tracks that does not decode as UTF-8 is skipped as binary. Both are holes in the sweep,
    which is why :func:`test_the_sweep_reads_the_tree_it_claims_to_read` asserts a floor on
    what came back: a rule that silently reads nothing passes forever.
    """
    listing = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found: list[tuple[str, str]] = []
    for name in listing.split("\0"):
        if not name or Path(name).suffix in BINARY_SUFFIXES:
            continue
        path = PROJECT_ROOT / name
        if not path.is_file():
            continue
        try:
            found.append((name, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return sorted(found)


def mentions(contents: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    return [
        (number, line.strip())
        for number, line in enumerate(contents.splitlines(), start=1)
        if pattern.search(line)
    ]


def report(found: list[tuple[str, int, str]], *, what: str) -> str:
    listed = "\n  ".join(f"{name}:{number}: {line[:120]}" for name, number, line in found)
    return f"{what}:\n  {listed}\n{THE_RULE}"


def test_no_private_file_is_tracked_at_all() -> None:
    """The half the gitignore already enforces, asserted anyway because it is the whole point.

    Mutation: ``git add -f docs-frank/reference/decisions.md``. A gitignore is a default and
    ``-f`` overrides it, so nothing but this notices. It is also what makes every other rule
    in this file safe to write: no tracked path is inside a private tree, so a sweep of
    tracked files never opens one.
    """
    inside = [name for name, _ in tracked_text_files() if name.split("/")[0] in PRIVATE_TREES]

    assert not inside, (
        f"these tracked files are inside a local-only tree: {inside}\n{THE_RULE}"
    )


def test_no_tracked_file_names_a_private_tree() -> None:
    """The rule the eighty-four citations broke.

    Mutation: write ``see docs-frank/reference/decisions.md`` into any comment.
    """
    found = [
        (name, number, line)
        for name, contents in tracked_text_files()
        if name not in NAMES_THEM_IN_ORDER_TO_FORBID_THEM and name not in THE_WRITE_FENCE
        for number, line in mentions(contents, PRIVATE_TREE)
    ]

    assert not found, report(found, what="these tracked files name a local-only path")


def test_the_write_fence_names_nothing_but_the_evidence_root() -> None:
    """The exemption is for a destination, not for a citation, and this is the difference.

    Mutation: cite a private document from ``tools/capture_phase2_evidence.py``. That file is
    on :data:`THE_WRITE_FENCE` because it has to name where a capture goes, and an exemption
    that let it also name a document would be an exemption for the whole rule -- fourteen
    files is enough room for the next citation to land somewhere nobody is looking.
    """
    found: list[tuple[str, int, str]] = []
    for name, contents in tracked_text_files():
        if name not in THE_WRITE_FENCE:
            continue
        for number, line in mentions(contents, PRIVATE_TREE):
            # ``tmp_path / "docs-frank" / "working" / "phase-0-evidence"`` is one path
            # written as three literals, and it is the fence in a test rather than a
            # citation. Rejoin it before asking what it names.
            if EVIDENCE_ROOT.search(line.replace('" / "', "/")):
                continue
            found.append((name, number, line))

    assert not found, report(
        found, what="these files may name the evidence root and named something else"
    )


def test_no_tracked_file_carries_a_home_directory() -> None:
    """Mutation: point a constant at ``/Users/<you>/projects-local/OLMo-core``.

    It passes on your machine, which is the whole difficulty: nothing downstream of it ever
    runs again anywhere else, and the skip it produces is indistinguishable from the honest
    one.
    """
    found = [
        (name, number, line)
        for name, contents in tracked_text_files()
        if name not in INVENTED_HOME_DIRECTORIES
        if name not in NAMES_THEM_IN_ORDER_TO_FORBID_THEM
        for number, line in mentions(contents, HOME_DIRECTORY)
    ]

    assert not found, report(found, what="these tracked files carry an absolute home path")


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        *(
            (name, "names them in order to forbid them")
            for name in sorted(NAMES_THEM_IN_ORDER_TO_FORBID_THEM)
        ),
        *((name, "on the write fence") for name in sorted(THE_WRITE_FENCE)),
        *((name, "an invented home directory") for name in sorted(INVENTED_HOME_DIRECTORIES)),
    ],
)
def test_every_exempted_file_still_exists(name: str, reason: str) -> None:
    """An exemption for a file nobody has is an exemption nobody is auditing.

    Mutation: rename a capture tool and leave its old name here. The rule would still pass,
    the renamed file would be swept, and the stale entry would sit in a list a reader trusts.
    """
    assert (PROJECT_ROOT / name).is_file(), f"{name} is exempted ({reason}) and is not here"


def test_the_sweep_reads_the_tree_it_claims_to_read() -> None:
    """The rules above are all assertions that nothing was found, so finding nothing by
    reading nothing passes every one of them.

    Mutation: return ``[]`` from :func:`tracked_text_files`. This repository tracks several
    hundred text files and every rule here would go green on an empty list, which is the
    shape of check this repository has removed more than a dozen times.
    """
    read = tracked_text_files()

    assert len(read) > 200
    assert any(name == "pyproject.toml" for name, _ in read)
    assert any(name.startswith("src/edullm_platform/") for name, _ in read)
