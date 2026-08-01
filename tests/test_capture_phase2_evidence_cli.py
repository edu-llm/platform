"""The laptop command that reads the lineage store and writes down what it holds.

Same shape as ``tests/test_capture_phase3_evidence_cli.py``: a stub ``aws`` on PATH
answering out of ``fixtures/evidence/phase-2/``, and the tool's output compared against
the committed records that supplied the answers.

**Only the lineage target, and that is a measurement rather than a stopping point.** Three
of this tool's five targets read GitHub through ``gh`` rather than an account through
``aws``, and the committed captures for them sit under ``github/`` while the tool writes
them at the output root -- so covering those means a second stub and a path mapping
invented here rather than read off the tool. Lineage is the target that carries the shared
write path: it is the one write in this tool that commits a document nobody in this
repository composed, and therefore the one that could commit an account id.

The third of those three, ``lead-team``, arrived on 2026-07-31, and its *write* is
uncovered here for the same reason as the other two rather than by oversight: what it
writes was checked by hand against a stub answering the members endpoint, and the record
it produced was byte-identical to the committed capture apart from the observation
instant. Its *read* is covered below, at the function rather than through ``main``,
because that read is the one place in this tool where getting less than everything is
silent: a member past a page boundary is simply not in the record, and the comparison that
would have named him reads the record.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.evidence import scan_for_secrets
from tools.capture_phase2_evidence import (
    ALLOWED_OUTPUT_SUFFIX,
    LEAD_APPROVAL_TEAM_SLUG,
    LINEAGE_BUCKET,
    capture_lead_team,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = PROJECT_ROOT / "fixtures" / "evidence" / "phase-2"
PROFILE = "sbsandbox"
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"

#: Compact rather than indented: this tool writes ``canonical_json_bytes``, because the
#: inventory is byte-compared against what the store holds.
OBSERVED_AT_FIELD = re.compile(r'"observed_at":"[^"]*"')
OBSERVED_AT_PLACEHOLDER = '"observed_at":"<when-the-capture-ran>"'


def inventory() -> dict[str, Any]:
    return json.loads((COMMITTED / "lineage.sanitized.json").read_text(encoding="utf-8"))


def stored_body(key: str) -> str:
    return (COMMITTED / "lineage" / "records" / key).read_text(encoding="utf-8")


def install_aws_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An ``aws`` that answers the three calls the lineage target makes, and nothing else."""
    recording = tmp_path / "aws-calls.txt"
    objects = inventory()["objects"]
    listing = {
        "Contents": [{"Key": entry["key"]} for entry in objects],
    }
    branches = [
        (
            f"  \"s3api list-objects-v2 --bucket {LINEAGE_BUCKET}\"*)\n"
            f"cat <<'RESPONSE'\n{json.dumps(listing)}\nRESPONSE\n    ;;"
        )
    ]
    store = tmp_path / "lineage-store"
    for entry in objects:
        key = str(entry["key"])
        head = {
            "VersionId": entry["version_id"],
            "ChecksumSHA256": entry["checksum_sha256"],
            "ContentLength": entry["content_length"],
        }
        branches.append(
            f'  "s3api head-object --bucket {LINEAGE_BUCKET} --key {key} "*)\n'
            f"cat <<'RESPONSE'\n{json.dumps(head)}\nRESPONSE\n    ;;"
        )
        source = store / key
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(stored_body(key), encoding="utf-8")
        # get-object names its destination as the last positional argument, which is $7
        # here: s3api get-object --bucket B --key K <destination>.
        branches.append(
            f'  "s3api get-object --bucket {LINEAGE_BUCKET} --key {key} "*)\n'
            f"    cp '{source}' \"$7\"; printf '{{}}'\n    ;;"
        )
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
        'case "$*" in\n' + "\n".join(branches) + "\n  *) exit 64 ;;\nesac\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def install_gh_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pages: list[list[dict[str, str]]],
) -> Path:
    """A ``gh`` answering the members endpoint in the shape ``--slurp`` returns.

    One outer array holding one array per page, which is what the flag documents and what
    the capture flattens. A stub that answered with a flat list would agree with the code
    that reads only the first page, so the shape here is the whole point of the stub.
    """
    recording = tmp_path / "gh-calls.txt"
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "gh",
        f"printf '%s\\n' \"$*\" >> '{recording}'\n"
        f"cat <<'RESPONSE'\n{json.dumps(pages)}\nRESPONSE\n",
    )
    monkeypatch.setenv("PATH", f"{stub_bin}{os.pathsep}{os.defpath}")
    return recording


def output_dir(tmp_path: Path) -> Path:
    return tmp_path / ALLOWED_OUTPUT_SUFFIX


def capture(tmp_path: Path, *arguments: str) -> int:
    return main(
        [
            "--aws-profile",
            PROFILE,
            "--aws-region",
            REGION,
            "--output-dir",
            str(output_dir(tmp_path)),
            *arguments,
        ]
    )


def written(tmp_path: Path) -> dict[str, str]:
    root = output_dir(tmp_path)
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def without_the_observation_instant(text: str) -> str:
    return OBSERVED_AT_FIELD.sub(OBSERVED_AT_PLACEHOLDER, text)


@pytest.mark.slow
def test_capturing_the_lineage_store_writes_the_inventory_and_every_body_beside_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The inventory says what S3 attests about each object; only the body says what the
    # platform decided, and the criteria about record content have to read one.
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "lineage") == 0

    assert set(written(tmp_path)) == {
        "lineage.sanitized.json",
        *(f"records/{entry['key']}" for entry in inventory()["objects"]),
    }


@pytest.mark.slow
def test_the_inventory_written_is_the_one_committed_for_the_same_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "lineage") == 0

    assert without_the_observation_instant(
        written(tmp_path)["lineage.sanitized.json"]
    ) == without_the_observation_instant(
        (COMMITTED / "lineage.sanitized.json").read_text(encoding="utf-8")
    )


@pytest.mark.slow
def test_a_body_is_committed_exactly_as_the_store_handed_it_over(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Byte for byte, including whether it is canonical: the digest the inventory quotes
    # has to describe the bytes beside it, and a body reformatted on the way out would
    # make the two disagree while both looked fine.
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "lineage") == 0

    files = written(tmp_path)
    for entry in inventory()["objects"]:
        key = str(entry["key"])
        assert files[f"records/{key}"] == stored_body(key), key


@pytest.mark.slow
def test_the_account_is_masked_out_of_a_stored_body_before_it_is_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A lineage body is text nobody in this repository composed: an execution ARN, a role
    # ARN, a message from a service. None of the ten committed today carries an account,
    # so the masking has nothing to do against them and this is where it gets something.
    install_aws_stub(tmp_path, monkeypatch)
    key = str(inventory()["objects"][0]["key"])
    leaking = tmp_path / "lineage-store" / key
    leaking.write_text(
        json.dumps({"role": f"arn:aws:iam::{ACCOUNT_ID}:role/somebody"}),
        encoding="utf-8",
    )

    assert capture(tmp_path, "--target", "lineage") == 0

    committed_body = written(tmp_path)[f"records/{key}"]
    assert ACCOUNT_ID not in committed_body
    assert scan_for_secrets(committed_body) == committed_body


@pytest.mark.slow
def test_no_account_id_reaches_any_file_the_lineage_capture_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_aws_stub(tmp_path, monkeypatch)

    assert capture(tmp_path, "--target", "lineage") == 0

    for name, text in written(tmp_path).items():
        assert ACCOUNT_ID not in text, name


@pytest.mark.slow
def test_the_lead_team_capture_records_the_members_of_every_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: read the first page and call it the team.

    A member GitHub returns on the second page is a reviewer on the lead gate exactly as
    much as one on the first. Reading only the first is not a smaller record: for a login
    ``config/organization.yaml`` does not declare, it is the same record as one taken of a
    team that never had him, and the comparison that exists to name him reads only what
    was captured.
    """
    install_gh_stub(
        tmp_path,
        monkeypatch,
        [[{"login": "philote-dev"}, {"login": "hiyasvyas"}], [{"login": "alsy7009"}]],
    )

    membership = capture_lead_team("edu-llm", "platform")

    assert membership.member_logins == ("alsy7009", "hiyasvyas", "philote-dev")


@pytest.mark.slow
def test_the_lead_team_capture_asks_github_for_every_page_of_the_team(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: drop --paginate, or drop --slurp, and trust ``per_page``.

    ``per_page=100`` is a ceiling rather than a promise, and the test above cannot see the
    difference on a team that fits: it passes on a single request for as long as the team
    is small, which is the condition under which somebody would remove the flags. The
    request is pinned for that reason. ``--slurp`` is what makes ``--paginate`` readable
    back, since bare ``--paginate`` emits one JSON document per page.
    """
    recording = install_gh_stub(tmp_path, monkeypatch, [[{"login": "philote-dev"}]])

    capture_lead_team("edu-llm", "platform")

    asked = recording.read_text(encoding="utf-8").split()
    assert "--paginate" in asked, asked
    assert "--slurp" in asked, asked
    assert f"orgs/edu-llm/teams/{LEAD_APPROVAL_TEAM_SLUG}/members?per_page=100" in asked, asked


def test_the_capture_refuses_to_write_outside_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A capture reads a live account and is local until somebody has read it and copied
    # what they want into fixtures/. The refusal is a paragraph rather than a token
    # because an operator who typed an absolute path into the wrong checkout has to be
    # able to tell which of the two constraints refused them.
    recording = install_aws_stub(tmp_path, monkeypatch)
    elsewhere = tmp_path / "somewhere-else"

    exit_code = main(
        [
            "--aws-profile",
            PROFILE,
            "--aws-region",
            REGION,
            "--target",
            "lineage",
            "--output-dir",
            str(elsewhere),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.err.splitlines()[0] == "output_dir_outside_working_directory"
    assert str(ALLOWED_OUTPUT_SUFFIX) in captured.err
    assert not elsewhere.exists()
    assert not recording.exists()
