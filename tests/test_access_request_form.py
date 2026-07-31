"""The form that asks a new person for their details, against the record those details go in.

The two have nothing to do with each other mechanically. ``PersonRef`` is a contract in the
library and the access form is YAML GitHub renders, and a field added to the first does not
reach the second. The failure that produces is quiet and lands on the wrong person: the form
stops asking for something the roster needs, whoever is setting up the new user finds the
entry incomplete, and either they know to go back and ask or the roster gets a guess in it.

There is no test for the other three issue forms and this is not the start of a policy of
testing forms. The reason this one has a test is that it is the only form whose fields are
supposed to correspond to a model in this repository, so it is the only one that can be
wrong rather than merely out of date.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from edullm_platform.contracts.inventory import PersonRef

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORM_PATH = PROJECT_ROOT / ".github" / "ISSUE_TEMPLATE" / "access-request.yml"


def form() -> dict[str, object]:
    loaded: dict[str, object] = yaml.safe_load(FORM_PATH.read_text(encoding="utf-8"))
    return loaded


def field_ids() -> set[str]:
    body = form()["body"]
    assert isinstance(body, list)
    return {str(element["id"]) for element in body if "id" in element}


def test_the_form_asks_for_every_detail_the_roster_records_about_a_person() -> None:
    """Mutation: add a field to PersonRef and leave the form asking for the old set.

    Named against the model rather than against a list of three strings, so the assertion
    keeps meaning what it says after somebody adds a fourth field. A test spelling out
    ``{"github_login", "display_name", "wandb_username"}`` would pass the day the model grew
    and go on passing, which is the failure it would exist to catch.
    """
    missing = set(PersonRef.model_fields) - field_ids()

    assert not missing, (
        f"config/organization.yaml records {sorted(missing)} about a person and "
        f"{FORM_PATH.name} does not ask for it, so whoever sets up a new user has to know to "
        "ask out of band. Add the field to the form, or if the roster should stop recording "
        "it, take it off PersonRef."
    )


def test_the_form_says_that_a_guessed_wandb_name_is_worse_than_no_name() -> None:
    """Mutation: drop the warning, or reduce it to "optional".

    The one field here that fails silently, and the only one where the helpful-seeming
    instinct is the harmful one. W&B honours an attribution only when the named account is in
    the service account's parent team, and says nothing when it is not -- the run logs as the
    platform, which is indistinguishable from an unattributed run. So a person who guesses to
    be accommodating produces the outcome they were trying to avoid, and finds out weeks
    later when their runs are not where they expected.

    ``PersonRef`` already argues this where the field is declared. The argument has to be on
    the form too, because the person filling it in is not reading the contract.
    """
    body = form()["body"]
    assert isinstance(body, list)
    wandb_field = next(element for element in body if element.get("id") == "wandb_username")
    description = str(wandb_field["attributes"]["description"])

    assert "Blank is a real answer" in description
    assert "guess is worse than blank" in description
    assert "service account" in description


def test_the_form_is_not_required_to_be_filled_in_before_somebody_has_an_account() -> None:
    """Mutation: mark the W&B field required.

    The field most likely to be made required by somebody tidying, and the one that must not
    be. This form exists to be filled in by people who do not yet have access to anything; a
    required W&B username makes it unfillable by exactly the person it is for, and the way
    that resolves in practice is that they invent one.
    """
    body = form()["body"]
    assert isinstance(body, list)
    wandb_field = next(element for element in body if element.get("id") == "wandb_username")

    assert wandb_field.get("validations", {}).get("required") is False
