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
    "SELF_SERVICE_KINDS",
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
            f"teaching the platform {kind!r} means {ADD_KINDS[kind]}, and that is not "
            "something this can open a pull request for. It lands across several reviewed "
            "files and, for some of them, a stack no workflow may deploy, so the "
            "decomposition is the platform's rather than yours. edullm ask files it, and "
            "edullm ask --help prints the kinds it takes. Say what you want rather than how "
            "it should be built."
        ),
    )
