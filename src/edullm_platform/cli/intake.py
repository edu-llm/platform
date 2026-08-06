"""``add`` and ``ask``: the two ways somebody changes what this platform will accept.

**THEY ARE TWO VERBS AND NOT ONE, AND ``docs-frank/reference/decisions.md`` SETTLES WHY.**
Teaching the system about a thing, such as a repository, a dataset, a shape, a model or a
person, produces a config change that is permanent, shared and self-service, with an agent
writing the pull request. Asking for something for yourself, such as a credential the
platform does not hold, an unusual resource or an escalation for work the platform cannot
express, produces a time-boxed grant to one person and genuinely needs a human. The single
self-routing verb was proposed twice and rejected: the two acts do not feel the same to the
person making them, and folding them makes a pull request the researcher could have written
themselves into a queue.

**NEITHER IS SPELLED ``request``.** ``contracts/authorization.py`` owns that word.
``RequestFacts`` and ``classify_request`` mean "a submission under judgement", so
``edullm request`` would mean two different things in adjacent paragraphs of one design.

**FOUR OF THE FIVE KINDS OF ``add`` REFUSE, AND THE REFUSAL IS THE FEATURE.** The buildout
spec defers the intake surface beyond ``add repository``, so there is nothing behind the
other four. Leaving them off the parser answers ``edullm add dataset`` with argparse's
"invalid choice", which tells an agent the word is wrong when the word is right. A refusal
carrying a code says the true thing in the vocabulary a skill already matches on, and it
costs no network to say.

**AND THE REFUSAL NAMES NO TEMPLATE FILE.** The four intake templates are collapsing into one
under a plan this module does not own, so a path written here goes stale on somebody else's
merge. ``edullm ask`` is the address, and it prints its own kinds.
"""

from __future__ import annotations

from typing import Final

from edullm_platform.cli.preflight import Refusal

__all__ = [
    "ADD_KINDS",
    "ASK_KINDS",
    "SELF_SERVICE_KINDS",
    "issue_body",
    "register_repository_form",
    "routed_to_ask",
]

#: The five things the platform can be taught, and what teaching it one means. The set is
#: ``system-overview.md``'s under "What you click" and is not this module's to extend: a kind
#: added here with nothing behind it is a promise the binary cannot keep.
ADD_KINDS: Final[dict[str, str]] = {
    "repository": "a codebase the platform may build an image from and run",
    "dataset": "a corpus a run may name, and the role it may be named in",
    "shape": "a machine the catalog prices and a queue can place",
    "model": "a set of weights a run may resolve as an input",
    "person": "somebody on the roster, with the team they belong to",
}

#: The kinds a pull request can be opened for without asking anybody first. One today, and
#: the registration workflow is what makes it one: ``register-repository.yml`` edits five
#: platform files, runs a local verification and opens the pull request. Nothing equivalent
#: exists for the other four.
SELF_SERVICE_KINDS: Final = frozenset({"repository"})


def routed_to_ask(kind: str) -> Refusal:
    """Why this kind is not something a pull request can be opened for from here.

    One code over the four rather than one per kind, which is ``DeniedOutrightError``'s
    argument in ``edullm_platform.errors``: the kind is already on the command line, so a
    code per kind would name in the vocabulary a thing the caller already said. What the
    detail adds is what the kind means and where the act goes instead.
    """
    return Refusal(
        code="add_kind_is_not_self_service",
        detail=(
            f"file this with edullm ask, and say what you want rather than how it should be "
            f"built. Teaching the platform {kind!r} means {ADD_KINDS[kind]}, which lands "
            "across several reviewed files and a stack no workflow may deploy, so no pull "
            "request can be opened for it from here."
        ),
    )


def register_repository_form(
    *,
    repository: str,
    github_repository_id: str,
    reason: str,
    dockerfile_path: str,
    default_branch: str,
) -> dict[str, str]:
    """``register-repository.yml``'s inputs, filled in from the checkout somebody is in.

    THE THREE REQUIRED INPUTS ARE FILLED AND THE OPTIONAL ONES ARE LEFT ALONE, WHICH IS THE
    POINT OF THE SPLIT. ``base_image_repository`` and ``base_image_digest`` default to the
    base two registrations already share and to the digest an existing registration of that
    base carries, which is the reviewed one. Sending a value from a laptop would be a second
    base to review, scan and re-pin, chosen by whoever happened to run the command.

    ``reason`` is required and has no default anywhere. It is written into a comment above
    the entry and it answers a question nothing else can: why this needs a repository of its
    own rather than a workload in an existing one.
    """
    return {
        "repository": repository,
        "github_repository_id": github_repository_id,
        "reason": reason,
        "dockerfile_path": dockerfile_path,
        "default_branch": default_branch,
    }


#: The kinds of ask the intake forms offer, which are also the labels a counter groups on.
#:
#: **A COPY, DELIBERATELY, WITH A TEST HOLDING IT TO THE SOURCE.** An installed wheel carries
#: no ``.github/``, so this cannot be read at runtime, and reading it over the network would
#: put a call in front of an ask somebody is already annoyed enough to be making.
#: ``tests/test_cli_ask.py`` reads ``.github/ISSUE_TEMPLATE/*.yml`` and asserts set equality,
#: which is the same seam ``ADMISSION_JOB`` sits on. The four templates collapse into one
#: triage form under a plan this module does not own, and the day that lands this list is red
#: rather than quietly filing asks under labels nothing counts.
ASK_KINDS: Final[tuple[str, ...]] = (
    "access-request",
    "dataset-request",
    "feedback",
    "run-problem",
)


def issue_body(
    *,
    detail: str,
    submitter: str | None,
    version: str | None,
    config_directory: str,
    run_id: str | None,
) -> str:
    """What somebody typed, and the three facts they could not have known to include.

    **THE FOOTER IS THE REASON THIS VERB BEATS OPENING THE FORM IN A BROWSER.** Half of what
    is asked here is about a refusal, and the first questions anybody answering one asks are
    which ``edullm``, which reviewed configuration and who. A stale install checking against
    the frozen copy it was built with is the commonest cause of a refusal that looks wrong,
    and it is invisible from the sentence somebody types. The form cannot ask for it and a
    person cannot be expected to volunteer it.

    Nothing else is collected. No paths, no repository name, no commit. The footer is four
    facts about the tool rather than about the work, which is the same line
    ``docs-frank/reference/designing-the-cli.md`` draws around a usage log.
    """
    lines = [detail.strip(), "", "---", "", "Filed by edullm."]
    lines.append(f"- who: {submitter or 'gh has recorded nobody'}")
    lines.append(f"- edullm: {version or 'an install with no recorded version'}")
    lines.append(f"- reviewed configuration: {config_directory}")
    if run_id is not None:
        lines.append(f"- run: {run_id}")
    return "\n".join(lines) + "\n"
