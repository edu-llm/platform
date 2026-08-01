"""How this repository depends on the dataset owner's reader, and why by commit.

``edullm_data.read`` is the only supported way to turn a dataset id into the S3 URIs a
training run opens and the dtype it must open them with. Writing our own resolver against
the published layout was the alternative and is worse in a specific way: the layout is that
repository's to change, and a second implementation of it would agree with the corpus right
up until the day it did not.

So this repository takes a dependency on somebody else's library, in a group of its own so
that nothing which does not read a corpus has to install it. What that dependency is pinned
to is the whole of this module.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

#: The commit the reader is pinned at, expanded from `38bf831` against the upstream
#: repository on 2026-08-01 rather than copied out of a plan.
PINNED_COMMIT = "38bf831a6c3f445e394784018441fd59288b876c"

#: The group name, kept in one place because two tests below assert about the same group and
#: a rename that updated one of them would leave the other passing against a group nobody
#: installs.
READER_GROUP = "reader"


def dependency_groups() -> dict[str, list[str]]:
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return document.get("dependency-groups", {})


def reader_requirement() -> str:
    group = dependency_groups()[READER_GROUP]

    reader = [entry for entry in group if entry.startswith("edullm-data")]
    assert len(reader) == 1, f"expected exactly one edullm-data requirement, found {reader!r}"
    return reader[0]


def test_the_reader_is_pinned_by_commit_because_the_tag_leaks_the_validation_set() -> None:
    """Mutation: pin @v0.2.0, which is the newest tag below the current line.

    NOT A MISSING FEATURE -- A LEAK. ``is_trainable`` does not exist anywhere in the library
    at v0.2.0, confirmed by reading that tag. The pinned commit uses it in four places in
    ``read.py`` to decide which splits a caller gets, so at v0.2.0 a request for training data
    is answered with held-out shards and no warning. Evaluating on the validation set is the
    kind of wrong that produces a better number rather than an error.

    A tag pin also gives up ``build_mixture``, ``MixtureSource``, ``numpy_dtype``, ``labels``
    and ``include_held_out``, none of which exist at v0.2.0 either. Those five are the reason
    this is a test and not a comment only in the sense that somebody would notice them
    missing; the leak is the reason it is a test, because nobody would.

    A version pin cannot reach the current code at all: upstream declared 0.5.0 at this commit
    and declares 0.6.0 on its mainline today, and no tag names either of those commits. Forty
    characters, because an abbreviated SHA is ambiguous by construction.
    """
    pin = reader_requirement()

    assert pin.endswith(f"@{PINNED_COMMIT}")
    assert "@v0.2.0" not in pin
    assert "@v0.1.0" not in pin
    assert len(pin.rsplit("@", maxsplit=1)[1]) == 40


def test_every_tag_upstream_publishes_names_a_commit_off_its_own_mainline() -> None:
    """Mutation: relax the rule above to "a tag is fine as long as it is recent".

    Measured against the upstream repository on 2026-08-01: v0.6.0, v0.6.1, v0.6.2 and v0.6.3
    are all present, all newer than the pinned commit, and NONE of them is an ancestor of that
    repository's main branch. They name work that is not on the line the corpora were
    published from.

    That makes tag-pinning worse here than the general argument against tags. The general
    argument is that a tag can move; this one is that following the newest tag would install
    code the upstream mainline does not contain, so "upgrade to the latest release" is not a
    step towards the code that produced the bytes in the bucket, it is a step sideways off it.

    Recorded as an assertion about our own pin rather than as a network check, because a test
    that queried GitHub would fail on an aeroplane and would be measuring their branch layout
    rather than our decision. What is asserted is the decision the measurement produced.
    """
    pin = reader_requirement()

    assert "@v" not in pin.rsplit("/", maxsplit=1)[-1], (
        "the reader must not be pinned to a tag: every tag upstream publishes names a commit "
        "that is not an ancestor of its own main branch, so a tag pin installs code the line "
        "the corpora were published from does not contain"
    )


def test_the_reader_group_is_separate_so_the_validator_package_never_carries_it() -> None:
    """Mutation: put edullm-data in [project.dependencies] beside pydantic and PyYAML.

    Two things install this repository and neither of them reads a corpus. The admission
    validator and the lifecycle recorder are built by tools/build_admission_lambda.py and
    tools/build_lifecycle_lambda.py, which resolve wheels for x86_64-manylinux and ship them
    inside a zip with a hard size limit -- and a git dependency is not a wheel on an index,
    so a runtime dependency here would be resolved from GitHub at build time by two builders
    that have no reason to know the reader exists.

    It belongs to the training container, which installs it deliberately. Keeping it in its
    own group is what lets `uv sync` stay the same command for everybody who is not training.
    """
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    runtime = document["project"]["dependencies"]
    assert not [entry for entry in runtime if "edullm-data" in entry]
    assert not [entry for entry in dependency_groups()["dev"] if "edullm-data" in entry]
    assert READER_GROUP in dependency_groups()


def test_the_pin_is_fetchable_by_a_resolver_rather_than_merely_well_formed() -> None:
    """Mutation: write the pin as `edullm-data==0.5.0`, or drop the `git+https://` scheme.

    The three tests above are all about which commit; this one is about whether a resolver can
    reach it. `edullm-data` is not on PyPI, so a bare version specifier resolves to nothing
    with a message about no matching distribution -- which reads like a typo rather than like
    a private repository, and is the kind of error somebody fixes by inventing a version
    number that does exist.
    """
    pin = reader_requirement()

    assert pin.startswith("edullm-data @ git+https://github.com/edu-llm/edullm-data@")
