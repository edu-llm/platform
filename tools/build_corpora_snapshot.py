"""Reduce a reading of the sealed dataset library to the measurement ``edullm data`` prints.

**WHY THE ANSWER IS A COMMITTED FILE RATHER THAN A LOOKUP.** ``edullm`` reaches no network
and holds no AWS credential, by design and by the absence of any trust policy that would
give it one. Fifteen of the thirty-five people on the roster hold no AWS role at all, so a
verb that read the bucket would answer for twenty of them and refuse the rest -- and it is
the fifteen who have no other way to see what corpora exist. So the measurement travels with
the tool. That is ``tools/build_run_history.py``'s argument, applied to the other reading
this platform commits.

**THIS TOOL MAKES NO AWS CALL AND THAT IS THE DESIGN RATHER THAN A LIMITATION.** It takes a
*reading* -- a directory of sealed ``dataset.json`` documents, laid out as the bucket lays
them out -- and reduces it. Fetching the reading is one command, run by somebody who has
already assumed the researcher role:

```bash
python tools/enter_researcher_lane.py
aws s3 cp --recursive s3://edullm-data/ /tmp/sealed/ \\
  --exclude "*" --include "*/dataset.json" --include "*/tokens/manifest.json"
uv run python tools/build_corpora_snapshot.py --reading /tmp/sealed
```

Splitting it that way buys three things. The reduction is testable against a directory a
test builds, which a boto3 call is not. The reading is a thing somebody kept, so two people
reducing the same reading get the same file. And the credential stays in the one place the
platform puts credentials, which is a person who assumed a role on purpose, rather than
becoming a dependency of a tool anybody might run.

**WHAT IS NOT IN THE OUTPUT, AND IT IS THE COLUMN THE VERB WAS BUILT FOR.** Nothing here
writes down whether a corpus will run. That verdict is a join over ``config/datasets.yaml``
and ``edullm_platform.tokenizers.TOKENIZERS``, both of which the wheel carries and both of
which are on the release trigger, so it is recomputed on every printing and cannot go stale.
A stored verdict would be a claim made on the day somebody ran this, and the day OLMo-core
grows a byte tokenizer it would be a wrong claim with nothing to prompt a re-run.
``edullm_platform.corpora`` carries the argument in full.

**IT REFUSES TO WRITE A ROW FOR A CORPUS THE REGISTRY DOES NOT CARRY, AND REPORTS THE ONES
IT DID NOT COVER.** The sealed bucket holds thirty-two datasets and the registry carries
twenty-nine, so a reading is routinely wider than the file, and a row nothing can name is a
row no verb will ever print. The other direction is the one worth being loud about: a
registered corpus this reading missed prints as dashes for ever, and
``tests/test_corpora_snapshot.py`` fails when the committed file leaves one uncovered.

Exit codes follow the repository's convention. 0 reported, 2 the inputs could not be read.
There is no 1, because this tool judges nothing and so has nothing to refuse.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.corpora import (
    CORPORA_FILENAME,
    CorporaSnapshot,
    CorpusMeasurement,
    as_document,
)
from edullm_platform.reviewed_configuration import ConfigFile

__all__ = [
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "ReadingUnusableError",
    "build_parser",
    "main",
    "measurement_from",
    "read_a_reading",
    "report",
]

EXIT_OK = 0
EXIT_UNUSABLE = 2

#: The group role a corpus's payload sits under, as the dataset standard spells it. The
#: measurement is about the payload rather than about a companion group, which is the same
#: distinction the registry already makes when it pins one group's ``manifest_sha256``.
PAYLOAD_ROLES = ("tokens", "text", "conversations", "files", "records")


class ReadingUnusableError(RuntimeError):
    """The reading is not where this expects it, or holds a document it cannot parse."""


def read_a_reading(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Every sealed ``dataset.json`` under a directory, keyed by dataset id and version.

    The key is the pair rather than the path, because the registry resolves a corpus by
    ``{dataset_id, version}`` and matching on a path would make this depend on how somebody
    happened to invoke ``aws s3 cp``.
    """
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(root.rglob("dataset.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReadingUnusableError(f"{path} could not be read: {exc}") from exc
        if not isinstance(document, dict):
            raise ReadingUnusableError(f"{path} is not a JSON object")
        dataset_id = str(document.get("dataset_id") or "")
        version = document.get("version")
        # Upstream's ``version`` is an object -- {"id": "v3", "relation": ..., "of": ...} --
        # and the registry stores the id. Both spellings are accepted rather than one being
        # assumed, because the seal shapes in this bucket already come in three varieties.
        version_id = str(version.get("id") if isinstance(version, dict) else version or "")
        if dataset_id and version_id:
            found[(dataset_id, version_id)] = document
    if not found:
        raise ReadingUnusableError(
            f"no dataset.json under {root}, so there is nothing to reduce. The reading is "
            "what `aws s3 cp --recursive s3://edullm-data/ <dir> --include \"*/dataset.json\"` "
            "produces; this tool makes no AWS call of its own."
        )
    return found


def measurement_from(reference_id: str, document: dict[str, Any]) -> CorpusMeasurement:
    """One sealed ``dataset.json``, reduced to the seven facts a chooser wants.

    Every field is optional and absent means absent. A seal that records no licence is the
    ordinary case -- fourteen of the thirty-two declare ``{basis: unknown, id: null}`` -- and
    filling that with a plausible identifier is the one kind of fact this platform refuses to
    invent.
    """
    payload = _payload_group(document)
    declared = document.get("license")
    licence: dict[str, Any] = declared if isinstance(declared, dict) else {}
    return CorpusMeasurement(
        reference_id=reference_id,
        train_tokens=_partition_count(payload, "train"),
        train_tokens_exact=True,
        shard_dtype=_text(payload.get("dtype")) if payload else None,
        size_bytes=_count(payload.get("bytes")) if payload else None,
        licence=_text(licence.get("id")),
        share_alike=_share_alike(document),
        purpose=_text(document.get("purpose")),
    )


def _payload_group(document: dict[str, Any]) -> dict[str, Any]:
    groups = document.get("groups")
    if not isinstance(groups, list):
        return {}
    for role in PAYLOAD_ROLES:
        for group in groups:
            if isinstance(group, dict) and group.get("name") == role:
                return group
    return {}


def _partition_count(group: dict[str, Any], name: str) -> int | None:
    partitions = group.get("partitions")
    if not isinstance(partitions, list):
        return None
    for partition in partitions:
        if isinstance(partition, dict) and partition.get("name") == name:
            return _count(partition.get("count"))
    return None


def _share_alike(document: dict[str, Any]) -> bool:
    """Whether anything the seal says names a share-alike licence.

    **READ OUT OF THE NOTES AS WELL AS OUT OF THE LICENCE FIELD, WHICH LOOKS SLOPPY AND IS
    THE WHOLE VALUE OF THE COLUMN.** ``pretrain/reservoir-dolma2`` declares
    ``{basis: unknown, id: null}`` and its own notes record that stackexchange and finewiki
    are CC-BY-SA-4.0, finewiki additionally GFDL, and that the two together are 7.13 per cent
    of its train tokens. A reader of the licence field alone learns nothing; share-alike is a
    condition on redistributing a model and it is a condition that corpus carries.

    A substring test rather than a parse, because there is nothing structured to parse: the
    fact lives in prose somebody wrote by hand. It over-reports rather than under-reports, so
    a corpus flagged here is one somebody has to go and read, which is the right cost.
    """
    text = json.dumps(document).upper()
    return any(marker in text for marker in ("CC-BY-SA", "SHARE-ALIKE", "SHAREALIKE", "GFDL"))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    said = str(value).strip()
    return said or None


def _count(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def report(snapshot: CorporaSnapshot, *, registered: Sequence[str]) -> list[str]:
    """What was measured, in the order somebody deciding whether to commit it wants it.

    The uncovered corpora are listed rather than counted, because a registered corpus with no
    row prints as dashes on every terminal until somebody notices, and a count does not say
    which.
    """
    covered = {entry.reference_id for entry in snapshot.measurements}
    missing = sorted(set(registered) - covered)
    lines = [
        f"{len(snapshot.measurements)} of {len(registered)} registered corpora measured",
        f"measured_at {snapshot.measured_at.isoformat()} from {snapshot.measured_from}",
    ]
    if missing:
        lines.append(
            "the reading covered none of these, so edullm data prints dashes for them: "
            + ", ".join(missing)
        )
    rounded = sorted(
        entry.reference_id
        for entry in snapshot.measurements
        if entry.train_tokens is not None and not entry.train_tokens_exact
    )
    if rounded:
        lines.append("carrying a rounded token count rather than a read one: " + ", ".join(rounded))
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--reading",
        type=Path,
        required=True,
        help=(
            "a directory of sealed dataset.json documents, as aws s3 cp --recursive leaves "
            "them. This tool reaches no network"
        ),
    )
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
        help=f"where to write the measurement; defaults to <config-dir>/{CORPORA_FILENAME}",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="measure and print, and write nothing"
    )
    parser.add_argument(
        "--measured-from",
        default="s3://edullm-data, every sealed dataset.json",
        help="what the reading was taken from, printed beside every table this produces",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        reading = read_a_reading(options.reading)
        registry = load_yaml(options.config_dir / ConfigFile.DATASETS.value, DatasetRegistry)
    except (ReadingUnusableError, OSError, ValueError, TypeError) as error:
        print(f"corpora_snapshot_not_built: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    measurements = [
        measurement_from(entry.reference_id, document)
        for entry in registry.published
        if (document := reading.get((entry.dataset_id, entry.version))) is not None
    ]
    snapshot = CorporaSnapshot(
        measured_at=datetime.now(UTC),
        measured_from=options.measured_from,
        measurements=tuple(measurements),
    )
    if not options.dry_run:
        destination = options.write or (options.config_dir / CORPORA_FILENAME)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(as_document(snapshot), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print("\n".join(report(snapshot, registered=[e.reference_id for e in registry.published])))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
