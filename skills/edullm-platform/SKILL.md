---
name: edullm-platform
description: >-
  Runs work on the eduLLM platform through `edullm`, the command line tool that is the only
  supported way to reach the cluster from a laptop. Use when somebody asks to train,
  evaluate, tokenize, sweep, benchmark or queue anything on the cluster, on AWS Batch or on
  a GPU, when a submission was refused and they want to know why, when a run is in flight
  and they want its state, or when the platform does not yet carry this codebase.
---

# Running work on the eduLLM platform

`edullm` submits and follows runs on the eduLLM platform. It drives `git` and `gh` and
nothing else. Every AWS credential lives in a workflow whose trust policy pins it to one
file on `main`, so nothing on a laptop can obtain one.

**Never write a script that calls AWS.** No `boto3`, no `aws` CLI, no HTTP at an AWS
endpoint. For the people who hold no AWS role that fails, and for the people who do it
works and leaves no record of the run, which is the worse outcome of the two.

## Install it with this line and no other

```bash
uv tool install --force git+https://github.com/edu-llm/platform
edullm --version
```

`uv` is the installer and it is the only one that works here. Where the shell answers
`command not found: uv`, install uv first with `curl -LsSf https://astral.sh/uv/install.sh | sh`
and run the line above again. Do not reach for `pip` or `pipx` instead.

Two near misses that both look like they ought to work.

- `uv tool install edullm` answers `not found in the package registry`. The distribution and
  the executable are both called `edullm` now, but neither this project nor anything else at
  that name is published to an index, so there is nothing to resolve. The line above
  installs from git.
- `uv tool upgrade` does something different depending on how the tool was installed, which
  is why it is not the instruction here. `uv tool upgrade edullm` follows the git ref the
  install named: from the bare URL above it re-resolves the default branch and does upgrade,
  but from a release note's line, which pins that release's tag, it prints `Nothing to
  upgrade` and exits 0 however far behind the install is. **Re-running the install line
  above is the upgrade for either, so run that rather than working out which install this
  is.**

**One-time, and only for an install made before v4.2.2.** Until then the distribution was
called `edullm-platform` while the command was `edullm`, so `uv tool list` named something
nobody types and `uv tool uninstall edullm` answered `not installed` to somebody holding the
binary. An install of `edullm` does not replace an `edullm-platform` one, so clear the old
name **before** installing:

```bash
uv tool uninstall edullm-platform
uv tool install --force git+https://github.com/edu-llm/platform
```

That order and not the other one. Both entries own the same `edullm` executable and uv
deletes the file with the entry, so uninstalling afterwards leaves `uv tool list` reporting a
healthy `edullm` and nothing on the path. Re-run the install line if that has already
happened. Where the old name was never installed the uninstall exits 2 with ``error:
`edullm-platform` is not installed``, which is the expected answer.

Re-install before you trust an answer that matters. The tool carries its own copy of the
reviewed configuration, frozen at the release it was built from, and prices against that
copy rather than against the platform as it stands now. `config_directory` in the output
below names the copy this install is reading.

## What it needs from the machine it runs on

- **`gh`, logged in.** `edullm check` reads who you are out of `gh`'s own `hosts.yml` and
  refuses with `submitter_unknown` where there is nothing to read. `gh auth login` once.
- **A clone with an `origin` remote.** The remote is how the repository is named and the
  commit is what the image is built from.
- **Nothing else.** No AWS profile, no SSO session and no VPN, for anything on this path.

## Where the command comes from

There is no flag that carries the command. `.edullm/run.yaml` holds what is a property of the
code, and everything a run is charged against is supplied at submit time instead, because one
commit run by two people belongs to two teams.

```yaml
schema_version: 1
workload_profile: olmo-core-check
suggested_compute: gpu-1xt4
command: >-
  bash -lc 'python .edullm/time_attention.py "$EDULLM_RUN_ID"'
```

`edullm check` writes a first one where a registered repository has none, and it names the
workload profiles that repository has in a comment at the top. To change what runs, edit this
file, commit it and push it. `--compute` and `--workload` override its last two for one
submission without editing anything.

The container is given `$EDULLM_RUN_ID`, `$EDULLM_CHECKPOINT_DIR` and several more, and the
command is exec'd as typed. Wrap it in `bash -lc` where you want a variable to expand.

## The loop, and the order is the whole of it

```
- [ ] 1. edullm check --json, and read the document
- [ ] 2. Fix every refusal, then check again
- [ ] 3. Say what it will cost and who has to release it
- [ ] 4. edullm submit
- [ ] 5. edullm status --json
```

`check` reaches no network, queues nothing and answers in a fraction of a second. It lists
every refusal at once rather than one per attempt, so the loop is edit, check, edit, check.
`submit` spends a queue slot and, for most runs, a person's attention. Submitting to find
out what is wrong is a trade you cannot take back.

`edullm <verb> --help` prints what each verb takes. Read it rather than guessing a flag.
This file covers what the help cannot say.

## 1. Check, and read the document rather than the paragraphs

```bash
edullm check --json --experiment <slug> --dataset <release-or-none>
```

`--experiment` is a lower-case hyphenated name for the question this run is part of
answering. It registers nothing, so invent one. `--dataset` is a registered corpus, or the
literal word `none` where the run reads no corpus, which is what a smoke test, a
tokenization or an evaluation over existing checkpoints does. Absent and `none` are
different answers and only one of them is a statement.

`--json` prints one document on stdout whatever the outcome. **Read stdout on its own.** The
first `check` in a repository that has no `.edullm/run.yaml` writes one and says so on
stderr, so `edullm check --json 2>&1 | ...` turns that note into a parse error on the one
run where you least want one.

Branch on the exit code before you read anything.

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | it stands | go on |
| 1 | refused on the merits | read `refusals`, fix, check again |
| 2 | the tool could not be driven | the command or the install is wrong |
| 3 | the platform could not be asked | retry. This is the only one worth retrying |
| 130 | interrupted | nothing |

Every document carries `format_version`, `edullm_version` and `verb`. A `check` carries
these as well.

| Key | What it holds |
| --- | --- |
| `refused` | whether anything stopped it |
| `refusals` | a list of `{code, detail}`. **Match on `code`.** The detail is prose and gets reworded |
| `deferred` | checks a laptop cannot make, listed even when nothing is refused |
| `cost` | the five factors and their product |
| `retries` | null on a one-attempt run. Otherwise what the later attempts do and do not buy, with `said` as the sentence to quote |
| `approval_class` | who has to release it |
| `manifest` | exactly what would be submitted, including the command and the commit |
| `history` | what runs of this shape have taken, with `said` as the sentence to quote |
| `config_directory` | the reviewed configuration this install carries |

**Do not read `retries.resume_required` as a promise that a retry resumes.** It is the
workload profile's declaration, and nothing on the platform checks it against the codebase
that would have to honour it. Measured on 2026-08-06, two of the six registered repositories
declare it, pass every check here, and restart from step 0. Quote `said` rather than the
flag.

## 2. Fix every refusal

The `detail` names the field and usually the file. These are the ones you will meet.

| Code | What to do |
| --- | --- |
| `submitter_unknown` | `gh auth login`. Nothing can be priced until the roster can be asked about somebody |
| `no_experiment` | Pass `--experiment`. There is no default and there is not going to be |
| `no_dataset` | Pass `--dataset`, with `none` where the run reads no corpus |
| `experiment_not_a_slug` | Lower case, digits, single hyphens between words, none at either end |
| `team_is_ambiguous` | Pass `--team` with one of the groups the `detail` names, and with none it does not. It also names the one to use for work nobody will keep |
| `uncommitted_changes` | Commit or stash. The image is built from the commit, so what would run is not what is on disk |
| `commit_not_pushed` | Push to a branch named `edullm/<something>`. That push is what builds the image. If you just pushed, `git fetch` first, because this reads the refs this clone holds |
| `unregistered_repository` | The platform does not carry this codebase. Go to **When the platform does not carry this codebase** below |
| `unregistered_workload_profile` | The `detail` lists the registered ones. Pass `--workload` with one of those |
| `workload_profile_repository_mismatch` | That workload belongs to another repository. The `detail` lists the ones this repository has |
| `unregistered_dataset` | Run `edullm data` and pick one off it. Never invent a release id |
| `retired_dataset_release` | The corpus is registered and withdrawn. The `detail` names the version its owner calls current |
| `dataset_is_not_a_corpus` | This resolves to a tokenizer or another input rather than to something a run trains on |
| `unprovisioned_compute_profile` | The shape is priced and has no compute environment behind it, so no job on it can start. Pick another `--compute` |
| `process_per_device` | The command starts a different number of processes from the number of cards on the machine. Fix the launcher or pick a `--compute` that matches |
| `bfloat16_not_in_the_hardware` | The card is Turing and has no bfloat16 at all. Pick a shape whose card has it, or set the run to float32 |
| `checkpoint_path_not_in_command` | The workload promises a checkpoint a retry resumes from and the command never expands `$EDULLM_CHECKPOINT_DIR`. Point the program's save folder at it, under a shell so it expands |
| `retry_without_a_checkpoint_contract` | More than one attempt on a workload that checkpoints nothing means the retry restarts from the beginning. Drop `--attempts` or pick a workload that checkpoints |

Anything else, read the `detail`. It was written to be acted on and it usually names the
file to change.

**Never pass `--force` to get past a refusal.** Every refusal it skips is one admission
makes again from inside AWS, so it buys a queue wait rather than an outcome.

### Picking the corpus, which has its own verb

```bash
edullm data                  # the list, smallest first
edullm data <reference-id>   # one of them in full
edullm data --json           # the same under `corpora`
```

Reaches no network, exits 0, and carries what a chooser needs per corpus: train tokens, the
tokenizer, whether the shards are `uint16` or `uint32`, the licence, and whether a run naming
it will start.

**That last one is not the same as registered, and the gap costs a machine.** Some registered
corpora are current, in a trainable family and refused by nothing this platform checks, and a
run naming one compiles, classifies routine, spends an approval, allocates the machine, and
then the container cannot build a tokenizer for the tokens it just resolved and exits 69.
`--json` puts that under `verdict` per entry, as `runs`, `refused` or `exits_69`. Branch on
it before you submit.

Never find the corpora by naming a bad one and reading the refusal. That list is names only.

A corpus nothing registers is a person's job rather than a command: the entry pins a manifest
digest and a payload profile read off the sealed bucket, which needs an AWS role this binary
does not hold. `edullm add dataset` says so and refuses. File it with
`edullm ask --kind dataset-request`.

## 3. Three things a clean check does not promise

A clean `check` is worth a great deal and it is not a clean bill of health. These are the
gaps, in the order they cost the most.

**The dtype the code sets, rather than the dtype the command names.** The bfloat16 guard
reads the text of the command. A trainer that fixes its precision in code carries no
bfloat16 token in argv, so the guard sees nothing and a Turing card refuses the first kernel
that needs the format, after the run has been priced, released, admitted and given a
machine. OLMo-core's training entry points are exactly this case. **Write the dtype into the
command so the check can see it**, which turns a dead machine into a free refusal.

```bash
# The guard reads this and refuses on a card without bfloat16.
bash -lc 'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" train_module.dp_config.param_dtype=bfloat16'
```

**Whether EC2 will sell this account that machine.** Nothing in `check` reads it, and a job
whose shape never places sits in `RUNNABLE` with no error against it, which looks the same
as being queued behind somebody. The verdict ships with the install, so read it before you
pick a shape.

```bash
CONFIG=$(edullm check --json --experiment a-first-look --dataset none 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["config_directory"])')
grep -A1 'profile: gpu-8xa100' "$CONFIG/capacity.yaml"
```

| What `places` says | What it means |
| --- | --- |
| `reliably` | the machine starts |
| `after_a_wait` | it arrives, and the entry beside it says what the wait has been |
| `unreliably` | it may never arrive |

Several shapes the catalog still offers read `unreliably`. Read the shape rather than
assuming the catalog lists only machines you can get, because picking one of those is how a
researcher spends a night watching a queue that will not move.

**The two image checks under `deferred`.** They need the container registry, which needs a
credential this tool does not hold, so they are made at submit time instead. `check` lists
them on a clean run for that reason.

## 4. Say what it costs, and say the whole of it

`cost` carries five factors and their product.

```
maximum_compute_cost_usd = hourly_rate_usd x nodes x maximum_runtime_hours x maximum_attempts x cells
```

**Report `maximum_compute_cost_usd`.** A fan-out multiplies, and `cells` is where it
multiplies. `--fanout-size` with `--fanout-index-parameter` turns one submission into that
many machines running at once, and quoting the hourly rate, or the cost of a single cell, to
somebody who is about to approve the lot is misleading them about the size of what they are
approving.

Never quote a price, a runtime bound or a ceiling from memory or from a document, this one
included. Those numbers live in reviewed configuration files and move without anybody being
told. Read them out of `cost` on every check.

## 5. Say who has to release it

`approval_class` is the answer, and it is in the same document.

| `approval_class` | Who releases the run |
| --- | --- |
| `automatic` | nobody. It starts as soon as admission accepts it |
| `routine` | a team lead, who has to open the run page and approve |
| `exception` | a platform admin |

Two things send a run to a person however cheap it is. **A fan-out always does, whatever its
size.** So does anything the reviewed configuration prices above its automatic bound. Do not
work out which from a figure you remember. Read `approval_class`.

Tell the user, before you submit, what the total is and whether a person has to tap. A
submission left waiting at a gate overnight looks queued from the outside, and the person
who thinks their job is running finds out in the morning.

## 6. Submit

```bash
edullm submit --experiment <slug> --dataset <release-or-none>
```

It runs the same checks and then dispatches the submission workflow. It prints the workflow
run page, then the run id on a line of its own, then whether the run was released
automatically or is waiting at a gate. **Keep the run id.**

Nothing you can run releases a waiting submission. A person has to.

## 7. Follow it

```bash
edullm status --json <run-id>
```

This answers from GitHub and dispatches nothing, so it costs no runner and you may call it
in a loop. Read `admitted` and `needs_a_dispatch`. On a run still at a gate, `gate` and
`reviewers` name who is being waited on and `you_can_release` says whether the submitter is
one of them, which is the difference between telling somebody to wait and telling them to
open the run page.

When `needs_a_dispatch` is true the answer has moved into AWS and the rest of it costs a
workflow. `edullm status <run-id>` without `--json` pays for that, and `edullm logs <run-id>`
prints the last lines the run printed. Both are slow by construction because a workflow has
to start, and neither belongs in a loop. Neither has a `--json` and neither is getting one,
because what they print is a section of a job log with no structure under it.

`edullm cancel <run-id> --reason "<why>"` stops an admitted run, and the reason is what the
run's history records instead of a failure.

## The exploration lane is not the submission path

`edullm run` ships this working tree to a machine of your own and streams back the output of
the command after a bare `--`. `edullm shell` gives you a terminal on that same machine, and
`edullm shell --notebook` forwards a Jupyter to the laptop.

Nothing on that route is checked against the registry, priced, approved or written to a
lineage record. What comes off it is a thing somebody saw rather than a result anybody can
cite. That is the point of it, and it makes the lane the wrong answer to a refused
submission. Reach for `check` and `submit` for anything meant to count.

The lane also needs an AWS session on the laptop, which most researchers do not have. Its
refusal names AWS rather than the tool, so do not read it as the platform being broken.

## When the platform does not carry this codebase

`unregistered_repository` means the platform has no image to build and no workload a
submission can name. Registration is two halves and the repository half comes first, because
a configuration change that points at a Dockerfile nobody wrote is a change that cannot be
reviewed.

Write three files in the research repository.

- `.edullm/Dockerfile`. It installs the dependency set and does nothing at runtime, since
  the command a run executes comes from the submission. Keep it small, because every layer
  is rebuilt on every push to an `edullm/**` branch.
- A workflow that calls the platform's reusable build. **Check what it fires on.** A caller
  that fires only on `edullm/**` pushes never fires for a branch named anything else, which
  is how a registered repository ends up with no image while looking correct.
- A first `.edullm/run.yaml`, holding the command, the workload profile and a suggested
  machine. `edullm check` writes this itself once the repository is registered, so this copy
  is a placeholder, and it is what makes the change reviewable.

Then commit, push to a branch named `edullm/<something>`, and open the configuration pull
request.

```bash
edullm add repository --reason "<why this needs a repository of its own>"
```

`--reason` has no default and it is the only part a reviewer cannot derive for themselves.
Answer why this needs a repository rather than a workload profile in one already registered.

**Say plainly that nothing is registered yet.** The pull request has to be merged and then
deployed, and `edullm check` goes on refusing this repository until both have happened. A
merged configuration change does nothing in the account on its own.

The other kinds of `add`, which are a dataset, a model, a person or a shape, are not
self-service. `edullm add <kind>` refuses with the route it goes by instead, which is
`edullm ask`. File it and say what you want rather than how it should be built.

## Never

- Never call AWS. The tool is the interface and the workflows hold the credentials.
- Never pass `--force` to `submit`.
- Never quote a price, a bound, a ceiling or a count of approvers from memory or from this
  file. Run `edullm check --json` and read it out of the output.
- Never parse the paragraphs where the verb has a `--json`.
- Never edit `.edullm/run.yaml` to make a refusal go away without reading what the refusal
  says.
- Never commit a secret, a credential or a token into a research repository. The image is
  built from the commit.
- Never report the cost of one cell of a fan-out as the cost of the submission.
