---
name: edullm-platform
description: >-
  Runs work on the eduLLM platform through `edullm`, the command line tool that reaches the
  cluster from a laptop. Use when somebody asks to train, evaluate, tokenize, sweep,
  benchmark or queue anything on the cluster, on AWS Batch or on a GPU, when a submission
  was refused and they want to know why, when a run is in flight and they want its state,
  or when the platform does not yet carry this codebase.
---

# Running work on the eduLLM platform

`edullm` submits and follows runs on the eduLLM platform. It drives `git` and `gh`, and it is
the whole of what a laptop can reach: every AWS credential lives in a workflow whose trust
policy pins it to one file on `main`, so there is none for a script here to borrow. Where
somebody does hold an AWS role of their own, a script using it starts a machine that no
lineage record names, and going through the tool is what turns a run into a citable result.

## The verbs

| Verb | What it does |
| --- | --- |
| `edullm check` | Prices a submission from this working tree and lists every refusal. Reaches no network. Writes a first `.edullm/run.yaml` where a registered repository has none |
| `edullm submit` | Makes those same checks and then dispatches the submission workflow |
| `edullm status` | Your recent submissions, or one run described |
| `edullm logs` | The last lines one run printed |
| `edullm cancel` | Stops one admitted run, with a reason that goes on the record |
| `edullm data` | The registered corpora, and which of them a run can actually start |
| `edullm add` | Teaches the platform about a repository. Produces a configuration pull request |
| `edullm ask` | Files an ask for something you need. Produces an issue somebody answers |
| `edullm run` | Ships this working tree to a machine of your own and streams back the output of the command after a bare `--` |
| `edullm shell` | A terminal on that same machine, or a notebook on it with `--notebook` |
| `edullm stop` | Ends the machine those two started, and says what it ran up and where your files are |
| `edullm studio` | Opens the Studio space for one `--project` in your browser. Bare, it lists your spaces; `--stop` ends compute and keeps the disk |
| `edullm console` | Opens the AWS console in your browser, signed in as you |

`edullm <verb> --help` prints what each verb takes; this file covers what the help cannot.

## Install it with this line and no other

```bash
uv tool install --force git+https://github.com/edu-llm/platform
edullm --version
```

`uv` is the installer and it is the only one that works here. Where the shell answers
`command not found: uv`, install uv first with `curl -LsSf https://astral.sh/uv/install.sh | sh`
and run the line above again. `pip` and `pipx` do not reach this.

Two near misses that both look like they ought to work.

- `uv tool install edullm` answers `not found in the package registry`. The distribution and
  the executable are both called `edullm` now, but neither this project nor anything else at
  that name is published to an index, so there is nothing to resolve. The line above
  installs from git.
- `uv tool upgrade edullm` follows the git ref the install named, so what it does depends on
  how the tool got there: from the bare URL above it re-resolves the default branch and does
  upgrade, but from a release note's line, which pins that release's tag, it prints `Nothing
  to upgrade` and exits 0 however far behind the install is. **Re-running the install line
  above is the upgrade for either**, which is why it is the instruction here.

**One-time, and only for an install made before v4.2.2**, when the distribution was called
`edullm-platform` while the command was `edullm`. Clear the old name **before** installing,
in that order: both entries own the same `edullm` executable and uv deletes that file along
with the entry, so uninstalling afterwards leaves `uv tool list` reporting a healthy `edullm`
and nothing on the path. Re-run the install line where that has already happened. On a
machine that never had the old name the uninstall exits 2 saying so, which costs nothing.

```bash
uv tool uninstall edullm-platform
uv tool install --force git+https://github.com/edu-llm/platform
```

Re-install before you trust an answer that matters. The tool carries its own copy of the
reviewed configuration, frozen at the release it was built from, and prices against that copy
rather than the platform as it stands now. `config_directory` names the copy it is reading.

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

`edullm check` writes a first one where a registered repository has none, naming that
repository's workload profiles in a comment at the top. To change what runs, edit this file,
commit it and push it; `--compute` and `--workload` override its last two for one submission
without editing anything. The container is given `$EDULLM_RUN_ID`, `$EDULLM_CHECKPOINT_DIR`
and several more, and the command is exec'd as typed, so wrap it in `bash -lc` where you want
a variable to expand.

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

**`retries.resume_required` is a declaration rather than a promise.** It is what the workload
profile says, and nothing on the platform checks it against the codebase that would have to
honour it. Measured on 2026-08-06, two of the six registered repositories declare it, pass
every check here, and restart from step 0. Quote `said` rather than the flag.

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
| `unregistered_dataset` | Run `edullm data` and pick one off it. A release id that was guessed at will not be one |
| `retired_dataset_release` | The corpus is registered and withdrawn. The `detail` names the version its owner calls current |
| `dataset_is_not_a_corpus` | This resolves to a tokenizer or another input rather than to something a run trains on |
| `unprovisioned_compute_profile` | The shape is priced and has no compute environment behind it, so no job on it can start. Pick another `--compute` |
| `process_per_device` | The command starts a different number of processes from the number of cards on the machine. Fix the launcher or pick a `--compute` that matches |
| `bfloat16_not_in_the_hardware` | The card is Turing and has no bfloat16 at all. Pick a shape whose card has it, or set the run to float32 |
| `checkpoint_path_not_in_command` | The workload promises a checkpoint a retry resumes from and the command never expands `$EDULLM_CHECKPOINT_DIR`. Point the program's save folder at it, under a shell so it expands |
| `retry_without_a_checkpoint_contract` | More than one attempt on a workload that checkpoints nothing means the retry restarts from the beginning. Drop `--attempts` or pick a workload that checkpoints |

Anything else, read the `detail`. It was written to be acted on and it usually names the
file to change.

`submit --force` dispatches with refusals outstanding, and what it buys is a queue wait
rather than an outcome: every refusal it skips is one admission makes again from inside AWS,
where the same answer costs a runner and arrives later.

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

This verb is the only complete answer to what a run may name. The `unregistered_dataset`
refusal lists names and nothing else, so a corpus picked off one may still be among those
that exit 69, and a table in a guide is a table somebody typed on a day that has passed.

A corpus nothing registers is a person's job rather than a command: the entry pins a manifest
digest and a payload profile read off the sealed bucket, which needs an AWS role this binary
does not hold. `edullm add dataset` says so and refuses. File it with
`edullm ask --kind dataset-request`.

## 3. Three things a clean check does not promise

A clean `check` is worth a great deal and it is not a clean bill of health. These are the
gaps, in the order they cost the most.

**The dtype the code sets, rather than the dtype the command names.** The bfloat16 guard
reads the text of the command, so a trainer that fixes its precision in code carries no
bfloat16 token in argv, the guard sees nothing, and a Turing card refuses the first kernel
needing the format — after the run has been priced, released, admitted and given a machine.
OLMo-core's training entry points are exactly this case. **Write the dtype into the command
so the check can see it**, which turns a dead machine into a free refusal.

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
multiplies: `--fanout-size` with `--fanout-index-parameter` turns one submission into that
many machines at once, so the hourly rate, or one cell, understates what an approver is being
asked to approve. Read every one of those numbers out of `cost` on the check in front of you
rather than from memory or from this file — they live in reviewed configuration files and
move without anybody being told.

## 5. Say who has to release it

`approval_class` is the answer, and it is in the same document.

| `approval_class` | Who releases the run |
| --- | --- |
| `automatic` | nobody. It starts as soon as admission accepts it |
| `routine` | a team lead, who has to open the run page and approve |
| `exception` | a platform admin |

Two things send a run to a person however cheap it is. **A fan-out always does, whatever its
size.** So does anything the reviewed configuration prices above its automatic bound. Which
way a given run lands is a question for `approval_class` rather than for a figure you
remember, since the bound behind it moves.

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

## When the shape is backed by a capacity block

Some of the largest shapes cannot be obtained on demand at all, because EC2 sells
accelerated capacity to whoever asks first and this account loses. A capacity block is the
way round it: a dated window during which the machines are yours because somebody paid for
them in advance.
[Capacity blocks](https://github.com/edu-llm/platform/blob/main/guides/capacity-blocks.md)
says which shapes are bought that way and how one gets bought. What follows is only what
changes about the loop above when you are submitting into one.

**A block-backed shape is live between two dates, and outside them it is not a slow queue,
it is nothing.** There is no machine, so nothing places, and the intuition that a queue
eventually moves does not carry over. Once the window closes the shape is withdrawn from the
reviewed configuration and `edullm check` refuses it with `unprovisioned_compute_profile`,
which is the good outcome. The bad one is the fourth paragraph below.

**AWS begins terminating the instances half an hour before the block ends.** The usable
window is thirty minutes shorter than the number of hours bought, and that is AWS clearing
the machines for the next customer rather than anything this platform can soften. A run
sized to fill the window exactly does not overrun by half an hour, it is killed half an hour
early and loses everything not checkpointed by then. Size it against the hours bought minus
the half hour, with `--hours` where the workload's own bound is larger than that.

```bash
edullm check --json --compute <profile> --hours <hours-bought-minus-0.5> \
  --experiment <slug> --dataset <release-or-none>
```

**Submit before the window opens rather than on the day.** Batch accepts a job against a
block that is not yet active and places it the moment the machines appear, so admission, the
image resolution and the approval all happen on your own time instead of on paid time. The
approval is why this matters, because approval is a person. **Every block-backed shape
classifies as `exception` and needs a platform admin**, whatever the run costs and however
short it is — the shape decides that and not the price, so shrinking the run does not move
it. **The block bills from the moment it starts whether or not anybody has approved
anything**, so a submission left at a gate until an approver wakes up spends paid-for minutes
at the full block rate to produce nothing.

**Three things about your own code usually have to change, and two of them need a commit and
an image build.** The batch size and parallel degrees have to be re-sized for a card with
different memory and a different device count; the container gets materially less host memory
than the instance advertises, so size against the figure `edullm check --json` prints for the
profile rather than the spec sheet; and the Blackwell shapes need a CUDA and driver pairing
that a repository pinning a `torch` or `flash-attn` wheel built for Hopper does not satisfy.
The image is built from a commit, so any of that has to be merged and built **before** the
start date, and `edullm check` catches none of it except `process_per_device`.
[Capacity blocks](https://github.com/edu-llm/platform/blob/main/guides/capacity-blocks.md)
carries the per-shape figures and the order to do them in.

**A job submitted outside the window sits in `RUNNABLE` and looks exactly like one that is
merely queued.** This is the trap and it is the part worth remembering. There is no error
against the job, nothing in any log and no field saying the window has not opened yet or
closed yesterday, so what you get is a healthy-looking wait that never ends;
`edullm status --json` distinguishes nothing and `edullm logs` prints nothing, because a job
that never started has printed nothing. **What tells the two apart is the calendar rather
than the job.** Get the start and end from whoever bought the block and check that now is
between them, before spending an evening debugging a run that is only early.

**Checkpointing is mandatory here rather than advisable, and it is the resume that has to
have been tested.** A block does not pause while a bug is fixed, cannot be extended and
cannot be cancelled, so a crash partway through costs either the work since the last
checkpoint or the whole remaining window, decided entirely by whether the run comes back up
where it left off. Writing a checkpoint and resuming from one are different features and
only the first is exercised by writing one: `checkpoint_path_not_in_command` reads whether
the command expands `$EDULLM_CHECKPOINT_DIR`, which is the write, and says nothing about the
read. Kill a run on a cheap shape, restart it from what it wrote, and watch it pick up rather
than begin again — before the window, because inside it that experiment costs the block.

## The exploration lane is not the submission path

`edullm run` ships this working tree to a machine of your own and streams back the output of
the command after a bare `--`. `edullm shell` gives you a terminal on that same machine, and
`edullm shell --notebook` forwards a Jupyter to the laptop. `edullm stop` ends that machine,
and it terminates rather than stopping, so its own disk goes with it while the scratch prefix
survives for the next one.

Nothing on that route is checked against the registry, priced, approved or written to a
lineage record. What comes off it is a thing somebody saw rather than a result anybody can
cite. That is the point of it, and it makes the lane the wrong answer to a refused
submission. Reach for `check` and `submit` for anything meant to count.

Those two also need an AWS session on the laptop, which most researchers do not have, and the
refusal names AWS rather than the tool. `edullm studio` needs no such session and is the
easier route to the same instance types, with a disk that survives, one space per `--project`.

## When the platform does not carry this codebase

`unregistered_repository` means the platform has no image to build and no workload a
submission can name. Registration is two halves and the repository half comes first, because
a configuration change that points at a Dockerfile nobody wrote is a change that cannot be
reviewed.

**First, resolve the base image against the ones already approved.** This is the one question
a reviewer has to answer, so answer it before asking: read `config/repositories.yaml` in the
platform repository, list the base images existing registrations carry, and resolve this
codebase's dependency set against them. Where one of them satisfies it, say which, and say
that it is already reviewed. Where none does, name the single pin that forces a new base — a
new base is a second thing to review, scan and re-pin, so the reason for one is that pin. The
base the project's own Dockerfile happens to use is not by itself a reviewed answer.

Then write three files in the research repository.

- `.edullm/Dockerfile`. It builds from the base you resolved, installs the dependency set and
  does nothing at runtime, since the command a run executes comes from the submission. Keep
  it small, because every layer is rebuilt on every push to an `edullm/**` branch.
- A workflow that calls the platform's reusable build. **Check what it fires on.** A caller
  that fires only on `edullm/**` pushes never fires for a branch named anything else, which
  is how a registered repository ends up with no image while looking correct.
- A first `.edullm/run.yaml`, holding the command, the workload profile and a suggested
  machine. `edullm check` writes this itself once the repository is registered, so this copy
  is a placeholder, and it is what makes the change reviewable.

**Then set `AWS_ECR_PUBLISHER_ROLE_ARN` as a repository variable on the repository being
registered**, which the workflow you just wrote reads and which nothing gives you.
`gh variable set AWS_ECR_PUBLISHER_ROLE_ARN --repo edu-llm/<name> --body <the ARN>` does it,
with the ARN `infra/README.md` records for `sbsandbox-intern-edullm-ecr-publisher`. **It is
set per repository by hand and there is no organization variable behind it**, so the
repositories that already have one say nothing about this one and registering does not create
it. Nothing here can check it for you — a token scoped to `edu-llm/platform` is refused by
every other repository's variables endpoint — so the reusable build's first step refuses an
empty value with `publisher_role_arn_is_empty`. Seeing that names this as the step that was
missed, which is what left `edullm-p1` reading as registered and publishing nothing for days.

Then commit, push to a branch named `edullm/<something>`, and open the configuration pull
request.

```bash
edullm add repository --reason "<why this needs a repository of its own>"
```

`--reason` has no default and it is the only part a reviewer cannot derive for themselves.
Answer why this needs a repository rather than a workload profile in one already registered.

The verb prepares the pull request and does not open it, because this organization forbids
Actions from creating one. It prints the workflow run page and the compare URL to open it at,
and the run's job summary prints a body too long for a URL to carry, to copy into the
description.

**Say plainly that nothing is registered yet.** The pull request has to be merged and then
deployed, and `edullm check` goes on refusing this repository until both have happened. A
merged configuration change does nothing in the account on its own.

The other kinds of `add`, which are a dataset, a model, a person or a shape, are not
self-service. `edullm add <kind>` refuses with the route it goes by instead, which is
`edullm ask`. File it and say what you want rather than how it should be built.

## What is easy to get wrong

All of this is somewhere above, collected because it is what has cost people machines.

- **Prices, bounds, ceilings and counts of approvers move.** Read them out of
  `edullm check --json` on the run in hand rather than from memory or from this file.
- **One cell of a fan-out is not the cost of the submission.** Report
  `maximum_compute_cost_usd`.
- **`refusals[].detail` is prose and gets reworded.** Match on `code`.
- **`edullm data` is the only thing that says a corpus will actually start.** Registered is
  not the same as runnable.
- **A refusal usually names the field and the file.** Editing `.edullm/run.yaml` until the
  message goes away produces a run priced wrong rather than a run that is fixed.
- **The image is built from the commit**, so a secret, credential or token committed to a
  research repository is a secret in the image.
