---
name: submitting-a-run
description: >-
  Submits a training, evaluation or tokenization run to the eduLLM platform through the
  edullm CLI, reading refusal codes from the machine-readable output rather than from prose.
  Use when the user asks to run, train, evaluate, sweep or submit something on the cluster,
  when a submission was refused and they want to know why, or when a run is in flight and
  they want its state.
---

# Submitting a run

The platform takes a commit, not a working tree. A run names a commit in a registered
repository, the container is built from that commit, and the record names what ran. Anything
uncommitted is not part of the run.

## The loop

```
- [ ] 1. Check, and read the JSON
- [ ] 2. Fix every refusal
- [ ] 3. Check again until it is clean
- [ ] 4. Submit
- [ ] 5. Follow it
```

### 1. Check, and read the JSON

```bash
edullm check --json --experiment <slug> --dataset <release-or-none>
```

Exit 0 means it stands. Exit 1 means read `refusals`. Every entry is a `code` and a `detail`.
**Match on `code`.** The detail is written for a person and gets reworded.

`check` reaches no network, so run it as often as you like. It costs a fraction of a second.

Two entries under `deferred` are checks a laptop cannot make. A clean `check` is not a promise
that the submission will go through.

### 2. Fix every refusal

The `detail` names the field and usually the file. The common ones and what they mean.

| Code | What to do |
| --- | --- |
| `uncommitted_changes` | Commit or stash. The container is built from the last commit, so what would run is not what is on disk. |
| `commit_not_pushed` | Push. A push to an `edullm/**` branch is what builds the image. If you just pushed, `git fetch` first. |
| `no_experiment` | Pass `--experiment` with a lower-case hyphenated name. It registers nothing. |
| `no_dataset` | Pass `--dataset`. Pass `none` where the run reads nothing, which is what a check, a tokenization or an evaluation over checkpoints does. Absent and `none` are different answers. |
| `team_is_ambiguous` | Pass `--team`. The `detail` lists the ones the roster puts this person on. |
| `unregistered_repository` | This codebase is not registered. Switch to the `registering-a-repository` skill. |
| `process_per_device` | The command starts fewer processes than the machine has cards. Fix the launcher or pick a smaller `--compute`. |
| `bfloat16_not_in_the_hardware` | The chosen card cannot do the dtype the command asks for. Pick another shape or another dtype. |
| `unregistered_dataset` | Run `edullm data` and pick one off it. Do not invent a release id. |

Anything else, read the `detail`. It was written to be acted on.

### Picking a corpus, which is `edullm data` and never a refusal

```bash
edullm data                    # the list, smallest first
edullm data <reference-id>     # one of them in full
edullm data --json             # the same under `corpora`, with `verdict` per entry
```

It reaches no network and exits 0. **Registered is not runnable, and this is the only thing
that says which is which.** Some registered corpora are refused by nothing here and reach a
container that cannot build a tokenizer for the tokens it just resolved, which exits 69 after
the machine has been paid for. `verdict` is `runs`, `refused` or `exits_69`, and the short
output names the `exits_69` ones and says what each is waiting on.

Never discover the corpora by naming a bad one and reading the refusal. That list is names
only: no size, no tokenizer, no licence, and no sign of which of them will start.

A corpus nothing registers is a person's job rather than a command. `edullm add dataset`
refuses, because the entry pins facts out of the sealed bucket that need an AWS role this
binary does not hold. File it with `edullm ask --kind dataset-request`.

### 3. Check again until it is clean

Do not skip to submit with refusals outstanding, and do not reach for `--force`. Every refusal
`--force` skips is one admission makes again from inside AWS, so it buys a queue wait rather
than an outcome.

### 4. Submit

```bash
edullm submit --experiment <slug> --dataset <release-or-none>
```

It prints the workflow run URL, then the run id on a line of its own, then whether the run was
released automatically or is waiting at an approval gate. Keep the run id.

If it says the submission is waiting, a person has to tap. Nothing you can run releases it.

### 5. Follow it

```bash
edullm status --json <run-id>
```

This answers from GitHub and dispatches nothing, so it is free and you may poll it. Read
`admitted` and `needs_a_dispatch`.

When `needs_a_dispatch` is true the run has reached AWS and the rest of the answer costs a
runner. Run the same verb without `--json`, or `edullm logs <run-id>` for what it printed.
Those two are slow by construction, tens of seconds at least, because a workflow has to start.
Do not put either in a loop.

## Never

- Never call AWS directly. No `boto3`, no `aws` CLI. The binary is the interface.
- Never parse the human output when `--json` exists on that verb.
- Never quote a price, a runtime bound or a cost ceiling from memory. `edullm check --json`
  prints them under `cost`, out of the reviewed configuration.
- Never pass `--force`.

## Exit codes

0 stands, 1 refused on the merits, 2 the tool could not be driven, 3 the platform could not be
asked, 130 interrupted. Only 3 is worth retrying.
