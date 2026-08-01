"""Shape and entity checks for the stored W&B key.

No real key appears here. Every case is built from a placeholder of the same shape, which is
enough because nothing under test compares against a known key.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Self

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "verify_wandb_credential.py"

LOOKS_LIKE_A_KEY = (
    "wandb_v1_"
    + "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-abcdefghijklm"[:77]
)


def load() -> Any:
    specification = importlib.util.spec_from_file_location("verify_wandb_credential", TOOL)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_a_netrc_style_prefix_and_a_trailing_newline_are_both_named() -> None:
    """W&B prints the key beside the literal word `api`, and a heredoc adds the newline."""
    module = load()

    faults = module.what_looks_wrong("api" + LOOKS_LIKE_A_KEY + "\n")

    assert any("`api`" in fault for fault in faults), faults
    # Whitespace first: it survives a correct copy of the key itself.
    assert "whitespace" in faults[0]


def test_a_well_formed_key_reports_no_faults() -> None:
    module = load()

    assert module.what_looks_wrong(LOOKS_LIKE_A_KEY) == []
    assert module.what_looks_wrong("0" * module.LEGACY_KEY_LENGTH) == []


def test_the_report_does_not_carry_the_key() -> None:
    module = load()

    described = module.describe(LOOKS_LIKE_A_KEY)

    assert described["length"] == len(LOOKS_LIKE_A_KEY)
    assert described["prefix4"] == "wand"
    assert len(described["fingerprint"]) == 8
    rendered = json.dumps(described)
    assert LOOKS_LIKE_A_KEY not in rendered
    assert LOOKS_LIKE_A_KEY[9:] not in rendered


def test_an_empty_secret_stops_at_the_first_fault() -> None:
    module = load()

    faults = module.what_looks_wrong("   \n")

    assert faults[-1] == "the stored value is empty"
    assert not any("`api`" in fault for fault in faults)


def test_an_unrecognised_shape_is_reported_rather_than_rejected() -> None:
    module = load()

    faults = module.what_looks_wrong("something-else-entirely")

    assert len(faults) == 1
    assert "neither a service-account key" in faults[0]


def test_a_null_viewer_reads_as_a_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """W&B answers an unrecognised key with 200 and a null viewer, so the status is no use."""
    module = load()

    class Response:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"data": {"viewer": None}}).encode()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: Response())

    answer = module.ask_wandb_who_this_is(LOOKS_LIKE_A_KEY)

    assert "entity" not in answer
    assert "does not recognise" in answer["error"]


def test_a_key_for_another_entity_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key that authenticates to the wrong entity puts runs where nobody is looking."""
    module = load()

    class Response:
        def read(self) -> bytes:
            return json.dumps(
                {"data": {"viewer": {"entity": "somebody-else", "username": None}}}
            ).encode()

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: Response())
    monkeypatch.setattr(module, "read_the_secret", lambda *a, **k: LOOKS_LIKE_A_KEY)

    assert module.main(["--expect-entity", "eduLLM"]) == 1


def test_the_entity_checked_is_the_one_the_container_is_told() -> None:
    """Resolved from `execution.WANDB_ENTITY` at call time rather than copied here."""
    from edullm_platform.execution import WANDB_ENTITY

    module = load()

    assert module.build_parser().get_default("expect_entity") is None
    assert WANDB_ENTITY == "eduLLM"


def test_the_tool_exposes_a_parser() -> None:
    module = load()
    parser = module.build_parser()

    assert parser.parse_args([]).secret_name == module.SECRET_NAME
    assert parser.parse_args(["--offline"]).offline is True
