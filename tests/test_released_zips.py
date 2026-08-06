"""One tripwire over every function this repository releases, instead of one per function.

THE THIRD COPY OF THIS IDEA WAS ABOUT TO BE WRITTEN AND THE FOURTH FUNCTION HAD NONE AT ALL.
``tests/test_phase2_lambda_package.py``, ``tests/test_phase3_lifecycle_package.py`` and
``tests/test_janitor_package.py`` each carried a
``test_the_released_zip_is_the_one_this_tree_builds`` of its own, written in that order and
each one described in its own docstring as mirroring the last. The notifier arrived after all
three and got none, so on 2026-08-06 it was the one function whose zip could move with nothing
going red -- and it had been drifting for an unknown period before anybody looked, from
``35a2634ce885`` against a recorded ``b55a71d58701``. It was found because somebody deployed
three functions by hand in #294 and noticed that only two of them had turned up in the pending
register.

**A register is only as complete as the tripwires that feed it, and a function with no
tripwire is not reported as fine -- it is not reported at all.** Nothing reads identically to
fine. That is why the fix is not a fourth copy: three copies of one idea is how the fourth
function comes to be missed, because "add the tripwire" is a step somebody has to remember
rather than something the register does on its own. The comparison below is parametrized over
``release_lambda.FUNCTIONS``, which is the table a function has to be in before a release can
be cut for it at all. A fifth function is covered on the day it is added to that table, which
is the day before it can first be uploaded.

**Three things are checked here and they answer three different questions.**

* :func:`test_the_released_zip_is_the_one_this_tree_builds` is the tripwire itself: does the
  zip this tree builds hash to what the release record says is deployed. Once per function.
* :func:`test_every_lambda_this_repository_declares_can_be_released` asks the question the
  other direction, and it is the one that would have caught the notifier on the day its
  template landed rather than three weeks later: is there an ``AWS::Lambda::Function`` in
  ``infra/`` that the release table does not name. A function CI can deploy and no record
  describes is a deployed artifact with nothing holding it to the tree.
* :func:`test_every_function_is_pointed_at_a_tripwire_that_runs` holds the citation each
  ``Function`` carries to a test that exists. The notifier's said
  ``tests/test_notifications_infrastructure.py``, which is a real file full of real
  assertions and contains no digest comparison at all, and both
  ``tools/verify_deployed_lambdas.py`` and ``tools/release_lambda.py`` believed it.

What stays in the three per-function modules is everything that is that function's own: which
configuration its archive carries, which entry point its template names, the platform its
wheels are built for, and the never-uploaded placeholder the janitor still holds in three
places. Those are properties of one function or of one builder and asserting them once each is
correct. The digest comparison was never one of them.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

from edullm_platform.pending_amendments import compare_release

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INFRA_ROOT = PROJECT_ROOT / "infra"

# The same path insertion the tools perform on themselves, and by bare module name for the
# reason tools/verify_deployed_lambdas.py gives at the same line: `tools.release_lambda` is a
# second entry in sys.modules holding an equal table that is not the same table, and what this
# module asserts about is the table the release tool actually reads.
TOOLS_DIRECTORY = PROJECT_ROOT / "tools"
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

import release_lambda
from release_lambda import FUNCTIONS, Function

#: The resource type a Lambda is declared as. Scanned for by type rather than by logical id or
#: by file name, because both of those are choices somebody makes and the type is not.
LAMBDA_RESOURCE = "AWS::Lambda::Function"


def infra_templates() -> list[Path]:
    """Every YAML file under ``infra/``, including the IAM ones.

    All of them rather than a declared list, which is the whole point: a list would have to be
    edited by whoever adds the fifth template, and that is exactly the edit that did not happen
    for the notifier's tripwire.
    """
    return sorted(INFRA_ROOT.rglob("*.yaml"))


def lambdas_declared_in(template: Path) -> list[str]:
    """The names every ``AWS::Lambda::Function`` in this template gives CloudFormation."""
    loaded = yaml.safe_load(template.read_text(encoding="utf-8"))
    resources = loaded.get("Resources", {}) if isinstance(loaded, dict) else {}
    return [
        resource["Properties"]["FunctionName"]
        for resource in resources.values()
        if isinstance(resource, dict) and resource.get("Type") == LAMBDA_RESOURCE
    ]


def parametrized_ids(test: object) -> list[str]:
    """The ids a parametrized test was declared over, read off the mark itself.

    Read rather than assumed, because the property being checked below is that a citation
    naming ``[notifier]`` selects something. A citation naming a parameter that does not exist
    is a pytest exit code 4 at the point of release, which is a worse failure than a red test
    and arrives at the worst moment.
    """
    for mark in getattr(test, "pytestmark", ()):
        if mark.name == "parametrize":
            return [str(one) for one in mark.kwargs.get("ids", ())]
    return []


def declared_tripwire(citation: str) -> tuple[Path, str, str]:
    """A ``path::test[id]`` citation split into the three things it names."""
    path, _, node = citation.partition("::")
    name, _, selected = node.partition("[")
    return Path(path), name, selected.removesuffix("]")


def code_block(function: Function) -> dict[str, object]:
    """The ``Code`` block of the one Lambda this function's template declares.

    Read by resource type rather than by logical id, so renaming the resource does not
    silently make the comparison below assert nothing.
    """
    loaded = yaml.safe_load(function.template.read_text(encoding="utf-8"))
    functions = [
        resource
        for resource in loaded["Resources"].values()
        if isinstance(resource, dict) and resource.get("Type") == LAMBDA_RESOURCE
    ]
    assert len(functions) == 1, f"{function.template.name} declares one Lambda"
    code = functions[0]["Properties"]["Code"]
    assert isinstance(code, dict)
    return code


def release_record(function: Function) -> dict[str, object]:
    loaded = yaml.safe_load(function.release_record.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


# ----------------------------------------------------------------------------------------
# The tripwire
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("selected", list(FUNCTIONS), ids=list(FUNCTIONS))
def test_the_template_pins_the_object_the_release_record_names(selected: str) -> None:
    """Reads BOTH files. Mutation: edit S3ObjectVersion in one of them and not the other.

    Two files name the deployed object and no reference connects them. Edited apart, the
    template points at a zip nobody recorded or the record describes a zip nobody deployed,
    and the digest comparison below then vouches for the wrong bytes -- which is worse than
    not having it, because it would report a release that did not happen.

    **THE NOTIFIER WAS MISSING THIS ONE TOO, WHICH IS THE SAME GAP TWICE.** The validator, the
    recorder and the janitor each carried a copy in their own package module and the notifier
    carried none: ``tests/test_notifications_infrastructure.py`` asserts that its template has
    *an* ``S3ObjectVersion`` and never compares it with ``infra/notifier-release.yaml``. So a
    release that pasted a version id into one file and not the other was, for that one
    function, a change nothing would have failed on. Written once here for the same reason the
    digest comparison is: a per-function copy is a per-function chance to not exist.

    Costs no build, so it is not marked slow and runs on every ``-m "not slow"`` pass.
    """
    function = FUNCTIONS[selected]
    code = code_block(function)
    recorded = release_record(function)

    assert code["S3Key"] == recorded["s3_key"]
    assert code["S3ObjectVersion"] == recorded["s3_object_version"]


@pytest.mark.slow
@pytest.mark.parametrize("selected", list(FUNCTIONS), ids=list(FUNCTIONS))
def test_the_released_zip_is_the_one_this_tree_builds(selected: str, tmp_path: Path) -> None:
    """Mutation: change a contract one of these functions imports and do not release it.

    THIS IS THE CHECK THE NOTIFIER DID NOT HAVE, AND THE ONE THE OTHER THREE PAID FOR. The
    validator's absence cost a live GPU run refused with ``unprovisioned_compute_profile`` by a
    validator holding the previous catalog: correct for the bytes that produced it, wrong about
    the account, and naming the compute profile rather than the release. The recorder was found
    on 2026-07-31 running bytes that matched neither the current tree nor the tree as it stood
    that morning, and every lineage record it wrote in that window was written by code nobody
    could point at. The notifier's is the third shape again: a message that is wrong about the
    money, posted into a channel where the only reader is a person who reads it once and
    believes it, with a correct lineage record sitting beside it giving them no reason to look.

    The build is deterministic, so this compares a digest rather than a timestamp: an unchanged
    tree rebuilds to the recorded digest and needs no edit. It fails only when the packaged
    bytes have moved and the record has not, which is exactly the window in which the account
    is running something this tree does not describe.

    **The digest is produced by the same call the release tool makes.** ``release_lambda.build``
    runs the builder that function's table entry names and reads the sha256 out of its JSON, so
    there is one definition of what this tree builds for a function rather than one here and
    one at the point of release. Two hashers are two chances to disagree, and the one that
    disagrees silently is the one in the test.

    The escape hatch is :func:`~edullm_platform.pending_amendments.compare_release` for all
    four, on the register's terms: a record that names the function, both digests and the
    command, that stops fitting the moment either digest moves, and that lapses. The janitor's
    own copy of this test declined the hatch on the argument that a tolerance recorded ahead of
    the thing it tolerates is a tolerance nobody can tell from a bug. That argument survives the
    move intact, because consulting the register is not recording anything in it: the register
    is empty, ``compare_release`` reports SKEWED for an unexplained difference exactly as a bare
    comparison would, and writing an entry for a function nothing is running would still be a
    deliberate act in a reviewed diff.
    """
    function = FUNCTIONS[selected]
    released = release_lambda.recorded_digest(function)

    assert released is not None, (
        f"{function.release_record.name} carries no readable sha256, so there is nothing to "
        "hold the zip against and this check would otherwise pass by comparing nothing. "
        "tools/release_lambda.py writes that line as part of cutting a release."
    )

    built = release_lambda.build(function, tmp_path / Path(function.s3_key).name)
    comparison = compare_release(selected, built=built, released=released)

    if comparison.waiting:
        pytest.skip(comparison.detail)

    assert comparison.holds, (
        f"the {function.name} zip this tree builds is not the zip that was released. "
        "Something the package carries has changed -- the handler, a contract it imports, or "
        "a file under config/ that this function's builder names -- and the deployed function "
        "is still running the previous bytes. Release it with "
        f"`uv run python tools/release_lambda.py --function {selected}` and land the new "
        f"version id and digest in {function.release_record.name} and "
        f"{function.template.name} in the same commit.\n\n{comparison.detail}"
    )


# ----------------------------------------------------------------------------------------
# That the tripwire covers everything there is
# ----------------------------------------------------------------------------------------


def test_every_lambda_this_repository_declares_can_be_released() -> None:
    """THE GENERALISATION, AND THE ONE THAT WOULD HAVE CAUGHT THE NOTIFIER ON DAY ONE.
    Mutation: add a fifth ``AWS::Lambda::Function`` to a template and release nothing for it.

    A function this platform deploys is a function some template under ``infra/`` declares,
    because CI deploys templates and nothing else creates one. So a template declaring a
    function that ``release_lambda.FUNCTIONS`` does not name is a deployed artifact with
    nothing holding it to the tree: no builder produces its zip, no record says what is in the
    account, the tripwire above never asks about it, and ``tools/verify_deployed_lambdas.py``
    never reads its digest. Every one of those is a silence rather than a finding.

    The account was read on 2026-08-06 to check this against something other than the tree,
    which is the only way to find out whether a count is complete. It holds twenty-five Lambda
    functions, of which three carry this platform's ``sbsandbox-intern-edullm-`` prefix -- the
    validator, the recorder and the notifier -- and the rest belong to the sixteen other teams
    in this shared sandbox. The fourth entry in the table, the expiry janitor, is built and
    recorded and has never been uploaded, which is what the placeholder in
    ``infra/expiry-janitor-release.yaml`` says and what ``tools/verify_deployed_lambdas.py``
    reports as ``deployed_lambda_absent``. So the tree's four is a superset of the account's
    three, which is the direction that is safe: every deployed function is in the table, and
    the table additionally covers one that is not deployed yet.

    Scanned rather than listed. A list of templates to scan would need editing by whoever adds
    the fifth one, and the failure being fixed here is precisely an edit somebody did not make.
    """
    declaring = {
        template: names
        for template in infra_templates()
        if (names := lambdas_declared_in(template))
    }
    releasable = {function.template: function.name for function in FUNCTIONS.values()}

    unaccounted = sorted(path.relative_to(PROJECT_ROOT) for path in set(declaring) - set(releasable))
    assert unaccounted == [], (
        f"{unaccounted} declare a Lambda function and tools/release_lambda.py cannot release "
        "one, so nothing builds its zip, nothing records what the account is running, and the "
        "tripwire in this module never asks about it. Add it to FUNCTIONS beside its builder, "
        "its template and its release record, and add lambda:GetFunctionConfiguration on it to "
        "infra/iam/audit-reader-role.yaml so the nightly can read its deployed digest."
    )

    missing = sorted(
        path.relative_to(PROJECT_ROOT) for path in set(releasable) - set(declaring)
    )
    assert missing == [], (
        f"{missing} are named by FUNCTIONS as the template that declares a Lambda and none of "
        "them declares one. A release cut for a function no template deploys edits an "
        "S3ObjectVersion nothing reads."
    )


def test_each_template_declares_exactly_one_function() -> None:
    """Mutation: put two functions in one template.

    Every reader here resolves a release record to a function by taking the one Lambda in the
    template beside it, and ``tools/release_lambda.py`` edits the one ``S3ObjectVersion`` line
    it finds. A second function in the same file makes each of those a guess, and a guess that
    lands wrong deploys one function's code under another's name.
    """
    several = {
        str(template.relative_to(PROJECT_ROOT)): names
        for function in FUNCTIONS.values()
        if len(names := lambdas_declared_in(template := function.template)) != 1
    }

    assert several == {}


def test_every_function_is_pointed_at_a_tripwire_that_runs() -> None:
    """THE DEFECT ITSELF, AS A CHECK. Mutation: cite a module that carries no comparison.

    That is not hypothetical and it is not a typo either. The notifier's entry named
    ``tests/test_notifications_infrastructure.py``, a real module with twenty real assertions
    about roles, queues, alarms and environment variables, and no digest comparison anywhere in
    it. So the field read as covered from every side: ``tools/release_lambda.py`` ran that
    module after a release and it passed, and ``tools/verify_deployed_lambdas.py`` sent anybody
    reading a drift finding to it to work out which side to change. Both were citing a test
    that does not exist.

    A citation is checked by resolving it rather than by looking at it, which is the difference
    between this and what was there before. The module has to import, the test has to be an
    attribute of it, and the parameter the citation selects has to be one the test was actually
    parametrized over -- because a node id naming a parameter that is not there is not a red
    test, it is `pytest` exiting 4 with "no tests ran", in the middle of a release.
    """
    for key, function in FUNCTIONS.items():
        path, name, selected = declared_tripwire(function.tripwire)

        assert (PROJECT_ROOT / path).is_file(), (
            f"the {function.name} cites {function.tripwire} and there is no such file"
        )
        module = importlib.import_module(path.stem)
        test = getattr(module, name, None)
        assert callable(test), (
            f"the {function.name} cites {function.tripwire} and {path.name} defines no {name}. "
            "A citation nothing resolves is what the notifier carried while its zip drifted: "
            "read by two tools, run by one of them, and holding nothing."
        )
        assert selected == key, (
            f"the {function.name} is keyed {key!r} in FUNCTIONS and cites the {selected!r} "
            "parameter, so a release of this function would run some other function's check"
        )
        assert selected in parametrized_ids(test), (
            f"{name} was not parametrized over {selected!r}, so {function.tripwire} selects "
            "nothing and pytest exits 4 rather than green or red"
        )


def test_the_tripwire_is_written_once_rather_than_once_per_function() -> None:
    """Mutation: give one function a tripwire of its own again.

    THE PROPERTY THIS WHOLE MODULE EXISTS FOR, ASSERTED RATHER THAN LEFT TO A REVIEWER. Three
    copies of one idea is what left the fourth function without it, and a fourth copy would be
    the same arrangement with a longer history. The one legitimate reason to move a function's
    tripwire elsewhere is that its digest question is genuinely different from these, and that
    is a change worth making deliberately here rather than one that arrives by a copy.
    """
    this_module = Path(__file__).relative_to(PROJECT_ROOT)
    elsewhere = {
        key: function.tripwire
        for key, function in FUNCTIONS.items()
        if declared_tripwire(function.tripwire)[0] != this_module
    }

    assert elsewhere == {}


def test_the_table_the_tripwire_reads_is_the_one_the_release_tool_edits(
) -> None:
    """Mutation: import ``tools.release_lambda`` here, which is an equal table and not this one.

    The same argument ``tools/verify_deployed_lambdas.py`` makes at its own import. Two spellings
    of one file are two module objects, and a test parametrized over the second one would go on
    passing for a function the release tool no longer has.
    """
    import verify_deployed_lambdas

    assert FUNCTIONS is release_lambda.FUNCTIONS
    assert verify_deployed_lambdas.FUNCTIONS is FUNCTIONS
    assert all(isinstance(function, Function) for function in FUNCTIONS.values())
