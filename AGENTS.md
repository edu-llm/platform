# Working in the platform repository

This is the eduLLM control plane, and this checkout is a maintainer's. Everybody who works
here can change what the platform enforces, deploy it, and read the account it runs in.
Nothing in this file withholds anything; it is here so that a session starts knowing what
this tree is and which of its habits have already cost somebody an afternoon.

[`MAINTAINING.md`](MAINTAINING.md) is the long form and carries the measurements. This is the
orientation.

## What is in here

| Path | Contents |
| --- | --- |
| `src/edullm_platform/` | The validation library and the `edullm` CLI. One implementation, shared by the submission workflow, the admission validator inside AWS and the tests |
| `config/` | Reviewed bindings — roster, approval policy, repository registry, workload and compute catalog, capacity verdicts |
| `infra/` | CloudFormation for everything this platform deploys, plus the runbook for procedures that need a laptop |
| `.github/workflows/` | Publishing a research image, submitting a run for admission, and the daily audit |
| `tools/` | Maintainer scripts, run by hand. Most of them hold an AWS session and read the account |
| `fixtures/` | Representative manifests, sanitized captures of what the account did, and recorded digests of both |
| `tests/` | The suite that keeps all of the above honest |
| `guides/`, `skills/` | The researcher-facing surface, which is written for somebody else |

## Two audiences, and only one of them is here

The distinction is worth holding onto, because most of the prose in this tree is addressed to
a reader who will never see it.

**A researcher never has this checkout.** They work in OLMo-core or a codebase of their own,
install `edullm` as a tool, and reach the platform through it because that is the only route
a laptop has: every AWS credential lives in a workflow whose trust policy pins it to one file
on `main`. What their agent loads is [`skills/edullm-platform/SKILL.md`](skills/edullm-platform/SKILL.md),
copied into their own repository by the lines in [`skills/README.md`](skills/README.md).
Editing that file is editing somebody else's always-on context, so it is held to the binary by
`tests/test_agent_layer.py` and it has a length budget.

**A maintainer is the only reader of this file.** The constraints that shape the researcher
skill are not constraints on the work here. `tools/` is full of boto3, capturing evidence and
verifying deployed stacks needs real credentials, and `infra/` is deployed from a laptop.

## Running the checks

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run pytest -q -n4 --dist loadgroup
uv run ruff check .
uv run mypy
```

`--dist loadgroup` is doing real work. Without the groups `tests/conftest.py` assigns, xdist
distributes individual tests, rebuilds every module's session fixtures on every worker, and
gives most of the parallelism back. `.github/workflows/ci.yml` runs exactly that line.

`-m "not slow"` looks like the fast path and is not one; a full parallel run beats it, and
`MAINTAINING.md` carries the measurements. The `slow` marker means *starts a subprocess*,
which is a real category and not a proxy for expensive.

## Five things that will bite

**`ruff format` rewrites most of this tree.** Nothing in CI runs it, `ruff check .` is the
whole of what lint means here, and no configuration makes the formatter a no-op — `line-length
= 100` already disagrees least. Reformatting is the right end state and the reason to defer it
is timing: it conflicts with every branch open at the time, so it wants a day when the branch
list is short, one commit that does nothing else, and `ruff format --check .` added to
`ci.yml` in that same commit.

**`ruff check .` respects `.gitignore`, and `mypy` does not.** A work-in-progress file listed
there is never linted by the standard command. Lint it by explicit path while working on it,
and before the `.gitignore` line comes out rather than after.

**A stale `.mypy_cache` outlives `.venv`.** It records where each module resolved from, so a
virtualenv built once on the wrong interpreter goes on producing that interpreter's answers.
It shows up as `boto3` being reported installed-but-untyped. Delete the cache before believing
a local mypy failure CI does not have. `uv venv --python 3.13 --managed-python` avoids causing
it.

**Contracts and their published schemas travel together.** `uv run python tools/export_schemas.py`
after changing any contract; the output is byte-reproducible, so a second run should be a
no-op.

**Recorded digests are a person's decision.** `tools/record_goldens.py` refuses to overwrite a
drifted digest and `--force` re-records. Read what moved first: a drift that was not intended
is a regression, and re-recording is the wrong repair. Review the digest diff in the same
commit as the change that caused it.

## Reaching AWS from here

The `edullm` CLI holds no AWS credential, by design, and that is a fact about the tool rather
than a rule about this repository. The maintainer scripts are the other half: `tools/` reads
the account directly, and several checks in the suite depend on what those scripts captured.

Captures under `fixtures/evidence/` expire after thirty days so a stale reading cannot pass as
current, and when they lapse the tests that read them go red on the pull-request path. Nothing
renews on its own — the remedy is re-running the capture tool with credentials, which for the
run records costs a read of the account rather than another publish.

`tools/verify_the_gate.py` is the one worth knowing by name. It reads the live approval
environments anonymously and is what stands between this repository being made private and the
approval gate silently ceasing to exist. `MAINTAINING.md` opens with why.

## Push a branch when its first commit exists

Not when the work is finished. `git push -u origin HEAD` on a branch nobody has reviewed
changes nothing on `main`, costs nothing, and is the only artifact another session can find. A
branch that exists only in a worktree is invisible to everybody, including whoever picks the
work up tomorrow, which is usually you with none of the context.

The evening of 2026-08-05 produced two cases and both had already cost the time they were
going to cost by the time anybody noticed. The repository registration tool was reworked,
finished, left unpushed, and then reworked from scratch hours later by a different session;
driving both implementations over the same templates produced byte-identical output, and the
second author had no way to know the first existed. A guard against module rebinding sat on one
laptop and no remote until it was rebased over eighty-one commits, whereupon it went red
immediately on a loader written days after the branch was cut, by somebody who had read neither
the incident nor the file describing it, because that file was in a worktree nobody could see.

Push first, keep pushing, and let the branch be the record. A draft pull request is better
still where the work is going to become one.

## Numbers live in configuration, not in prose

Every ceiling, rate, bound and count that reaches a terminal is interpolated from the loaded
configuration at the point of printing, and `tests/test_cli_no_hardcoded_bounds.py` fails the
build when one is written out. The rule is structural because the alternative has already
failed: the routine runtime bound disagreed between the documents and `config/policy.yaml`
three separate times, every one of them a second copy that was correct on the day it was
typed. The same rule reaches the documents an agent reads, through `tests/test_agent_layer.py`.

`uv run edullm check --json` in a research checkout prints those numbers from the loaded
configuration, which is what a document should point at instead of quoting.

## Where to go next

- [`MAINTAINING.md`](MAINTAINING.md) — the checks, what each one claims, and the evidence.
- `infra/README.md` — deployed stacks, roles, and the procedures that need a laptop.
- `src/edullm_platform/cli/main.py` — the verb tables, the exit codes, and why each is what it is.
- [`skills/edullm-platform/SKILL.md`](skills/edullm-platform/SKILL.md) — what a researcher's
  agent is told, which is the document to change when the CLI's surface moves.
- `docs-frank/` — local-only working notes. Gitignored, and it stays that way.
