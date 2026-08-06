"""How many asks of each kind are open, and which kinds have stopped being asks.

docs-frank/reference/system-overview.md, "What you click, and what generates it": one place
makes asks countable, "which turns the third identical one into a config change". The count is
not a metric -- it is that sentence made checkable. What the output is for is deciding what to
build, so what it leads with is the kinds that have crossed.

THERE IS NO EXIT CODE 1. tools/report_spend.py carries the same rule and gives the same reason:
a red job in a path something else depends on is one step away from being a control, and a
queue of asks getting long is not a reason to fail anything. 0 when the count was taken, 2 when
it could not be.

Through `gh api` rather than the AWS CLI, because this is a GitHub surface, and it runs on the
token a scheduled workflow already holds -- which makes this the only job on the audit that
needs no AWS credential at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import Field

from edullm_platform.cli.intake import ASK_KINDS
from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import ContractModel

__all__ = [
    "ASKS_CONFIG_PATH",
    "ASK_KINDS",
    "AskThresholds",
    "asks_worth_a_config_change",
    "build_parser",
    "count_by_kind",
    "main",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Re-exported rather than restated, and which copy is the source is not arbitrary. There are
#: three readers -- the dropdown in .github/ISSUE_TEMPLATE/ask.yml, `edullm ask --kind`, and
#: this counter -- and the CLI's copy is the one that has to be a literal, because an installed
#: wheel carries no .github/ and cannot read the form at runtime. So the package holds the list
#: and everything else reads it: tests/test_triage_form.py holds the dropdown equal to it and
#: this module imports it. A kind added to the form and not here would be an ask filed under a
#: label nothing counts, and that failure is invisible rather than loud -- the board just never
#: shows the category.

ASKS_CONFIG_PATH: Final = "config/reports/asks.yaml"


class AskThresholds(ContractModel):
    schema_version: Literal[1]
    config_change_threshold: int = Field(gt=0)


def count_by_kind(issues: Sequence[Mapping[str, object]]) -> dict[str, int]:
    """How many open asks carry each kind label, including the kinds nobody has asked for.

    STARTS FROM ZEROS AND ADDS, which is what keeps the empty kinds in the answer. A kind with
    no asks is a real result and a different one from a kind this tool has never heard of; a
    mapping built only from labels that appeared makes the two indistinguishable, which is the
    same denominator argument 2026-08-04-the-instruments.md makes about the mismatch list.

    An ask is counted under every kind it carries. One can genuinely be two -- a dataset request
    that also reports the run it broke is both -- and taking the first label would attribute it
    to whichever one GitHub happened to return first.

    Labels that are not kinds are skipped rather than counted, and `ask` is the one that makes
    that matter: every issue this reads carries it, so counting labels rather than kinds would
    put a row on the board with the highest number on it and no meaning.
    """
    counts = dict.fromkeys(ASK_KINDS, 0)
    for issue in issues:
        labels = issue.get("labels", ())
        assert isinstance(labels, Sequence)
        for label in labels:
            assert isinstance(label, Mapping)
            name = str(label.get("name", ""))
            if name in counts:
                counts[name] += 1
    return counts


def asks_worth_a_config_change(counts: Mapping[str, int], threshold: int) -> tuple[str, ...]:
    """The kinds that have crossed, largest first, ties broken by name.

    Inclusive, because "the third identical one" means three and a strict comparison would
    report on the fourth -- which is the overview's rule moved by one without anybody deciding
    to move it.

    Ordered by count so the largest is first, since the output is read by somebody choosing what
    to build. Ties broken by name so two runs over the same data print the same thing, which is
    what makes the audit's step summary diffable.
    """
    crossed = [(count, kind) for kind, count in counts.items() if count >= threshold]
    return tuple(kind for _count, kind in sorted(crossed, key=lambda pair: (-pair[0], pair[1])))


def _open_asks(repository: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repository}/issues?state=open&labels=ask&per_page=100",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "gh api failed")
    # --paginate concatenates one JSON array per page, so parse defensively rather than
    # assuming one document. A single page is the common case and both shapes must work.
    text = completed.stdout.strip() or "[]"
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = [
            item
            for page in text.replace("][", "]\n[").splitlines()
            for item in json.loads(page)
        ]
    return list(parsed)


def build_parser() -> argparse.ArgumentParser:
    assert __doc__ is not None
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", default="edu-llm/platform")
    parser.add_argument("--json", action="store_true", help="machine-readable instead of markdown")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    thresholds = load_yaml(PROJECT_ROOT / ASKS_CONFIG_PATH, AskThresholds)
    try:
        issues = _open_asks(arguments.repository)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"asks_unreadable: {error}", file=sys.stderr)
        return 2

    counts = count_by_kind(issues)
    crossed = asks_worth_a_config_change(counts, thresholds.config_change_threshold)

    if arguments.json:
        print(json.dumps({"counts": counts, "crossed": list(crossed)}, indent=2, sort_keys=True))
        return 0

    print("## Open asks, by kind\n")
    print(f"Examined {len(issues)} open issues labelled `ask`.\n")
    print("| Kind | Open | Worth a config change |")
    print("| --- | --- | --- |")
    for kind in ASK_KINDS:
        print(f"| `{kind}` | {counts[kind]} | {'yes' if kind in crossed else ''} |")
    if crossed:
        print(
            f"\n{', '.join(f'`{kind}`' for kind in crossed)} have reached the threshold "
            f"`{ASKS_CONFIG_PATH}` names. The overview's rule is that the third identical ask "
            "is a config change rather than a favour."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
