"""The one form four templates collapsed into, and the things it must not lose.

system-overview.md, "What you click, and what generates it", says one place makes asks
countable, which is what turns the third identical one into a config change. Counting is
tools/report_asks.py; this module holds the shape counting depends on -- one form, a kind on
every ask, and the kinds matching the vocabulary the CLI's `edullm ask` offers.

WHAT THIS REPLACES. tests/test_access_request_form.py held one of the four against PersonRef,
and its reasoning is kept rather than dropped: the access half of this form is still the only
part whose fields are supposed to correspond to a model in this repository, so it is still the
only part that can be wrong rather than merely out of date.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from edullm_platform.cli.intake import ASK_KINDS
from edullm_platform.contracts.inventory import PersonRef

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE"
FORM_PATH = TEMPLATE_DIR / "ask.yml"


def form() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(FORM_PATH.read_text(encoding="utf-8"))
    return loaded


def body() -> list[dict[str, object]]:
    found = form()["body"]
    assert isinstance(found, list)
    return found


def field(identifier: str) -> dict[str, object]:
    return next(one for one in body() if one.get("id") == identifier)


def field_ids() -> set[str]:
    return {str(one["id"]) for one in body() if "id" in one}


def test_there_is_exactly_one_issue_form() -> None:
    """Mutation: leave one of the four in place.

    The collapse is the deliverable. Four forms plus a fifth is worse than four, because the
    counter reads one of them and the other four stay uncounted while looking exactly as
    reachable to a requester.
    """
    forms = sorted(path.name for path in TEMPLATE_DIR.glob("*.yml") if path.name != "config.yml")

    assert forms == ["ask.yml"]


def test_the_form_asks_which_kind_of_ask_this_is() -> None:
    """Mutation: drop the dropdown and let the requester describe it in prose.

    Countability is the whole reason for one form. A free-text description of what somebody
    wants is not groupable, and the third identical ask is only visible as a config change if
    the first two were filed under the same label.

    Compared against the vocabulary the installed CLI carries rather than against a list
    written here, because `edullm ask --kind` and this dropdown are two doors into one queue
    and a kind reachable through one and not the other is an ask filed where nothing counts.
    """
    kinds = field("kind")

    assert tuple(kinds["attributes"]["options"]) == tuple(sorted(ASK_KINDS))
    assert kinds["validations"]["required"] is True


def test_the_form_routes_to_a_lead_before_the_owner() -> None:
    """Mutation: drop the lead field.

    system-overview.md's umbrella diagram routes work that fits no route to the submitter's
    lead and then to the owner. Without a named lead the ladder collapses to one rung and every
    ask is the owner's, which is the load the collapse exists to spread.
    """
    lead = field("lead")

    assert lead["validations"]["required"] is True
    assert "lead" in str(lead["attributes"]["label"]).lower()


def test_the_form_still_asks_for_every_detail_the_roster_records_about_a_person() -> None:
    """Mutation: lose a PersonRef field in the collapse.

    Carried over from tests/test_access_request_form.py with its reasoning intact: the roster's
    fields are the only ones on this form that correspond to a model here, and a form that
    stops asking for one leaves whoever sets up a new user guessing or asking out of band.
    Named against the model so it keeps meaning something after the model grows.
    """
    missing = set(PersonRef.model_fields) - field_ids()

    assert not missing, (
        f"config/organization.yaml records {sorted(missing)} about a person and ask.yml does "
        "not ask for it"
    )


def test_the_form_still_says_that_a_guessed_wandb_name_is_worse_than_no_name() -> None:
    """Mutation: shorten the W&B guidance to "optional" during the collapse.

    The one field that fails silently. W&B honours an attribution only when the named account is
    in the service account's parent team and says nothing when it is not, so a person guessing
    to be accommodating produces exactly the outcome they were trying to avoid and finds out
    weeks later.
    """
    description = str(field("wandb_username")["attributes"]["description"])

    assert "Blank is a real answer" in description
    assert "guess is worse than blank" in description
    assert "service account" in description


def test_the_wandb_field_is_not_required() -> None:
    """Mutation: mark it required while tidying.

    This form is filled in by people who have access to nothing yet. A required W&B username
    makes it unfillable by exactly the person it is for, and the way that resolves in practice
    is that they invent one.
    """
    assert field("wandb_username")["validations"]["required"] is False


def test_the_form_says_that_access_is_four_things_in_three_systems() -> None:
    """Mutation: drop the access preamble as the longest thing in the collapsed form.

    It is the most carefully written prose of the four templates and the only part of any of
    them that saves a whole afternoon: none of the four pieces of access fails by saying "you
    do not have access", and one of them fails silently. A collapse that tidied it away would
    look like a smaller form and cost exactly the person it is for.
    """
    preamble = "\n".join(
        str(one["attributes"]["value"]) for one in body() if one["type"] == "markdown"
    )

    assert "Write access on this repository" in preamble
    assert "A place on the roster" in preamble
    assert "Weights and Biases account" in preamble
    assert "Approval authority" in preamble


def test_every_ask_lands_in_the_one_queue_the_counter_reads() -> None:
    """Mutation: drop the `ask` label, or replace it with a kind.

    tools/report_asks.py asks GitHub for open issues labelled `ask` and then groups them by
    kind. A form issue that does not carry it is invisible to the count however it is
    otherwise labelled, and a form that carried one kind unconditionally would file every ask
    under it -- a GitHub issue form cannot set a label conditionally on a dropdown value, which
    is why the kind is the triager's one job.
    """
    labels = form()["labels"]

    assert labels == ["ask"]
