# Using the platform

Everything true whichever repository you work in. For what to actually run, see [training a model](olmo-core.md), [running an evaluation](olmo-eval-full.md) or [validating a corpus](edullm-data.md). If you would rather not open a browser at all, skip to [from a terminal](#from-a-terminal).

## Your first run

Open [Submit a run](https://github.com/edu-llm/platform/actions/workflows/submit-run.yml), press **Run workflow**, and fill in six fields. Everything else has a correct default.

| Field | Value |
| --- | --- |
| `repository` | `OLMo-core` |
| `commit_sha` | `main` |
| `workload_profile` | `olmo-core-check` |
| `compute_profile` | `cpu-32vcpu` |
| `team` | `scratch` |
| `experiment` | Anything, such as `onboarding` |
| `wandb_project` | Your Weights and Biases project |

**Type `main` and do not go looking for a branch.** The field takes a branch, a tag or a full commit, but your code has to have been built into a container image before a run can name it, and on your first run you have not built anything. `main` always has one. When you come to run your own work, *Building your image* in your repository's guide is the step that gets you there.

Leave `command` alone. Its default runs a one-line check needing no code of your own, which is the point of a first run: you are testing the path from the form to a machine and back.

Use `scratch` the first time. It is the bin for work nobody intends to keep, so a first run costs no research group anything.

## Access

Three lists, and the first two are the ones that gate anything. They are separate systems, which is the most confusing thing about getting started here.

| List | What it gates | If you are missing |
| --- | --- | --- |
| `team-members` GitHub team | Write access, and therefore whether the **Run workflow** button exists at all | The page has no button. Nothing you type will help |
| `members` in `config/organization.yaml` | Admission | Your submission is refused when it compiles, before any lead is asked, naming the file |
| A W&B account on the `eduLLM` team, recorded on the roster | Attribution only | The run works and logs as the platform rather than as you. Nothing warns you |

Ask for all three through the [ask](https://github.com/edu-llm/platform/issues/new?template=ask.yml) form, picking `access-request` as the kind, or run `edullm ask --kind access-request`.

## The submission form

| Field | What it decides |
| --- | --- |
| `repository`, `commit_sha` | What code runs. The commit must already have an image. See *Building your image* in your repository's guide |
| `workload_profile` | The time limit, the retry limit and the checkpoint contract, together. Not the machine |
| `compute_profile` | The machine. Required, and the field that decides what your run costs |
| `team` | Who reviews it, and the S3 prefix your output lands under. Closed dropdown: `platform`, `memory-split`, `input-core`, `pre-training`, `post-training`, `data-prep`, `eval-inference`, `scratch` |
| `experiment` | Groups related runs, such as a sweep, an ablation or a week. Free text, registers nothing, and what the cost view groups by |
| `dataset_release` | The corpus your job reads. `none` if it reads none |
| `command` | What the container runs. No shell: the first word must be a program name, and the line must not be wrapped in outer quotes |
| `wandb_project` | Where the run reports |
| `image_digest`, `maximum_runtime_hours`, `maximum_attempts`, the fan-out fields | Advanced. Leave them. The image comes from your commit, and the two bounds come from the workload profile |

## Choosing a machine

`compute_profile` is a closed dropdown of every shape with a queue behind it, and it is the most expensive field on the form by a factor of fifty-seven. The range runs from $0.53 an hour to $30.13. Nothing infers it from what you are running, and nothing refuses a small job on a large machine.

**Three shapes are priced in the catalogue and cannot be started.** `gpu-8xh100` and `gpu-1xh100` are the two that catch people, because eight H100s at $55.04 an hour is the number everybody remembers. EC2 has never once sold this account a p5 of either size, so both read `provisioned: false` and naming one is refused as `unprovisioned_compute_profile` before anything is dispatched. `gpu-1xa10g-sagemaker` is the third and nothing was ever built for it. The full list with the column that says so is in [training a model](olmo-core.md#multi-gpu-jobs).

| You are | Pick |
| --- | --- |
| Doing anything for the first time | `cpu-32vcpu` |
| Checking your code sees a GPU | `gpu-1xa10g` |
| Training, one device | `gpu-1xa10g` |
| Training, several devices | A `4x` or `8x` shape, and start one process per device, as your repository's guide shows |

**This used to say to leave the field alone, and that advice was wrong the whole time it was there.** The workload profile appeared to name a machine and the form outranked it silently, so a run labelled `olmo-core-train-1gpu` could land on eight H100s and nothing anywhere said so. The catalog no longer claims a machine and this field is required, so you are asked once rather than defaulted somewhere you did not choose.

Your image is unaffected by this field. It is built from your commit for one architecture and runs on every shape here, so picking a bigger machine changes what you are billed and nothing about what runs.

## Approval

There are two answers, and `edullm check` prints which one you got before you submit anything.

| What `check` prints | Who releases it |
| --- | --- |
| `automatic` | Nobody. It starts when you submit it |
| `routine` | Any of the eight team leads |

**One cell, under $500 worst case, and nobody releases it.** No lead, no wait. It is still recorded and still attributed to you, and you still have to be on the roster and running registered code. What you skip is the queue, not the checks.

**No hour bound decides this.** `olmo-core-train` at its full twenty-four hours and two attempts on one A10G is $48.29 and starts on its own. The rule reads the worst-case total, which already multiplies the rate by the hours by the attempts by the cells, so a long run is an expensive one and expensive is what the bound catches. The figure lives at `automatic_below_cost_usd` in `config/policy.yaml`, and it is strictly under. $499.70 starts on its own and $500.23 waits.

**What does bound your hours is the workload profile.** `--hours` above what the profile declares is refused with `runtime_above_the_workload_bound`, which names the profile and its figure. That refusal arrived on 2026-08-06. Until it did, `--hours 10000` against a one-hour profile was accepted, priced at $5,260 and routed to a lead who had no way to see that the profile said one.

**A fan-out never starts on its own, whatever it costs.** Four cells of a twenty-step check is $2.10 and still goes to a lead, because four cells is four machines starting at once and the total does not carry that. So does an image whose registry scan findings nobody has read yet.

**Do not quote that $500 anywhere.** It is reviewed configuration and it has already moved once, from $5. Run `edullm check --json` and read `approval_class` beside the cost it was decided on, because that is the copy your own install is being judged against.

Everything else waits for a person. Any of the eight team leads can release any group's run, so you are not blocked on one individual. But nobody is paged, so if a run has been waiting, ask.

**There is no admin tier and no rate ceiling.** A third class called `exception` exists in the code and no submission reaches it. It is kept for capacity blocks, which nothing has built. Eight A100s at $21.96 an hour for one hour is $21.96, and it starts on its own like anything else under the bound. If a page or a refusal sends you to find an admin, that page is out of date.

If you are approving, it is not a formality. Before you release a run you are shown its cost, its machine, the team it is booked to, whether the submitter will be attributed, and whether it waived any check.

## The run id

Every run gets an id like `run_019fbce3-…`. That one string is the name of the job on AWS, the folder your output lands in, and the name of your run in Weights and Biases. It is stable across retries, and it is the only thing anyone needs from you when something goes wrong.

## Guards and waivers

The platform refuses commands that contradict the run you asked for. Each guard exists because it cost somebody a real run. Asking for four GPUs and starting one process trains on a quarter of the machine, bills for all of it, and exits zero.

Override by putting a token in the command, which records the decision rather than routing around the check:

```
bash -lc 'EDULLM_LAUNCH_CHECK=waived python benchmarks/memory.py --batch 64'
```

There are two of them today and they are spelled the same way on purpose: `EDULLM_LAUNCH_CHECK=waived` for a multi-GPU machine running one process, and `EDULLM_CHECKPOINT_CHECK=waived` for a run that promised a checkpoint and saves somewhere the platform will not look. A command can carry both.

A waiver lands in the run's manifest and the approving lead is told which check was waived. What it does not do is make the underlying thing work. A waived checkpoint run that loses its machine starts from nothing.

## The corpora

| `dataset_release` | Train tokens | Objects |
| --- | --- | --- |
| `math-frontload-100m-v1` | 0.1B | 3 |
| `formal-proof-premises-500m-v3` | 0.5B | 12 |
| `fineweb-edu-750m-v2` | 0.7B | 15 |
| `fineweb-edu-1b-v6` | 1.0B | 4 |
| `fineweb2-phase0-equal-bpe-2b-v1` | 2.0B | 12 |
| `fineweb2-phase0-equal-superbpe-2b-v1` | 2.0B | 12 |
| `refhq-instruct-v3` | 3.9B | 29 |
| `refhq-regmix-5p5b-v2` | 5.5B | 24 |
| `regmix-10b-v1` | 10.0B | 41 |
| `frontload-cl-10b-v1` | 10.1B | 53 |
| `fineweb2-unimax-bpe-20b-v1` | 21.0B | 166 |
| `fineweb2-unimax-superbpe-20b-v1` | 18.9B | 151 |
| `olmo-original-30b-v1` | 31.3B | 120 |
| `olmo-127b-v1` | 126.5B | 474 |
| `olmo-150b-dolma2-v1` | 157.2B | 6,851 |
| `reservoir-dolma2-v1` | 250.2B | 10,010 |

All are frozen and nothing you run can write to them. Most use the dolma2 tokenizer, which the training image has built in. The exceptions are: `fineweb-edu-1b-v6` and `fineweb-edu-750m-v2` (SmolLM2 from Hub), `formal-proof-premises-500m-v3` (vendored Qwen2.5 from Hub), and the four `fineweb2-*` Plan B releases (gigatoken BPE / SuperBPE, configured in the image with no Hub fetch). Hub outages refuse only the SmolLM2 and Qwen corpora; Plan B and dolma2 start without it.

Two more things about `formal-proof-premises-500m-v3` are worth knowing before you report a number from it. Its shards are `uint32` rather than the usual `uint16`, which the loader must take from the manifest and never infer; and ATP/TPTP traces carry most of its token mass, so a single loss over the whole corpus is mostly measuring two of its six sources. The Plan B `fineweb2-*` shards are also `uint32` (100k-vocab gigatoken).

**`reservoir-dolma2-v1` is 977 GB and its licence needs reading before you publish anything trained on it.** Its licence field says the basis is unknown, and its own notes say more: stackexchange and finewiki are CC-BY-SA-4.0, finewiki additionally GFDL, and the two together are 7.13 per cent of its train tokens. Share-alike is a condition on redistributing a model, not just an unanswered question, so it is worth knowing before the run rather than after.

**`formal-proof-premises-500m-v2` came off this list on 2026-08-06 and v3 replaced it.** v3 supersedes v2 in the corpus's own sealed metadata, so naming v2 now is refused by `edullm check` and by the compile job, both before the approval gate, with a refusal that names v3. A run resuming from a checkpoint written against v2 is the case that refusal is meant to be liftable for; the route is a reviewed line in `config/datasets.yaml` rather than a flag on the command.

**More corpora are published than are offered here, and the reason is never the corpus.** `lean4-mathlib-bytes-v3` and `math-memory-full-v1` are sealed, frozen and readable, and they are tokenized with raw UTF-8 bytes, which OLMo-core has no tokenizer for. They stay in the registry and off this list until it does, because a run that resolved one would reach a container that cannot build a model for the tokens it just read. `fineweb-edu-1b-v6` was in that state until somebody wrote the one line naming its tokenizer, which is the difference between a missing upstream feature and a job nobody had done.

**Size costs nothing up front.** Shards are memory-mapped from S3 as the loader reaches them, so a 157B corpus starts as quickly as a 5B one and reads only what your step count needs. Pick by what you are training, not by what you can afford to download.

## Container environment

Set for you. You never supply these, and a value you set yourself is one the record will disagree with.

| Variable | What it holds |
| --- | --- |
| `EDULLM_RUN_ID` | This run's id, stable across retries |
| `EDULLM_CHECKPOINT_DIR` | Where checkpoints go |
| `EDULLM_OUTPUT_PREFIX` | Where everything else this run writes goes |
| `EDULLM_TEAM` | The team you claimed |
| `EDULLM_COMMIT_SHA` | The commit that was resolved and built |
| `EDULLM_DATASET_RELEASE` | The dataset you named |
| `EDULLM_DATASET_ID`, `EDULLM_DATASET_VERSION`, `EDULLM_DATASET_TOKENIZER` | Which published corpus that resolves to, and what tokenised it. Absent when you picked `none` |
| `AWS_BATCH_JOB_ARRAY_INDEX` | Which cell of a fan-out this is, counting from zero. Set by Batch, and only on a fan-out |
| `EDULLM_FANOUT_INDEX_PARAMETER` | What that index varies, copied from what you put on the form. Only on a fan-out |
| `WANDB_PROJECT`, `WANDB_ENTITY` | Read by the W&B client directly |

The three dataset variables come from the registry entry behind the field you picked rather than from anything you typed.

## Fan-out

A fan-out runs your command once per cell. Give the form a `fanout_size` and a `fanout_index_parameter` naming what varies, such as `seed` or `shard`.

Each cell gets its own output prefix. `EDULLM_OUTPUT_PREFIX` and `EDULLM_CHECKPOINT_DIR` already carry the `cell-<index>/` segment by the time your program starts, so a command that writes where it is told needs no change to be safe in a fan-out. A single run is unaffected and keeps the prefix it has always had.

```
teams/curriculum/runs/run_019fbce3-…/cell-0/
teams/curriculum/runs/run_019fbce3-…/cell-1/
```

Read `AWS_BATCH_JOB_ARRAY_INDEX` to decide what this cell should do, and `EDULLM_FANOUT_INDEX_PARAMETER` to know what you said it varies.

```
bash -lc 'python train.py --seed "$AWS_BATCH_JOB_ARRAY_INDEX" --save-folder "$EDULLM_CHECKPOINT_DIR"'
```

**How many cells run at once is not something you set.** Batch takes a size for an array job and no concurrency cap, so what bounds it is how much of the queue's capacity one cell reserves. A fan-out is priced and approved as one submission at its full size.

## Looking at a run, and stopping one

[Look at a run, or stop it](https://github.com/edu-llm/platform/actions/workflows/cancel-run.yml) does both. Nothing changes unless you tick **stop**.

| You want to | Do this |
| --- | --- |
| See your latest run | Leave `run_id` blank |
| See a specific run | Give it the run id. You can look at anybody's |
| Stop a run | Tick **stop** and give a reason. Your own, any time; admins can stop anyone's |

You get the status, why it is not running if it is not, the exit code, and the CloudWatch log stream name. Batch reports `RUNNABLE` both for a job waiting on a machine and for one asking for more machine than exists. The reason beside the status is what tells those apart. A queued job bills nothing, but nobody is watching the queue, so ask if yours has not started within an hour.

**Pressing cancel on Submit a run is not this.** That stops the workflow and leaves the job running. Come here with the run id instead.

The reason you give is recorded, so the run's history says it was cancelled rather than that it failed. Anything already written stays, checkpoints included, so stopping a run to fix its command does not throw away the hours it already did.

## From a terminal

The same loop without the Actions UI. One binary:

```
uv tool install --force git+https://github.com/edu-llm/platform
```

You need [uv](https://docs.astral.sh/uv/) and a `gh` that is logged in with `gh auth login`. That is the whole of it for the five verbs below. `check`, `submit`, `status`, `logs` and `cancel` drive `git` and `gh` rather than holding a credential of their own, so they can do what you can do and nothing more, and there is no AWS account anywhere in this. All five were run on 2026-08-06 with `AWS_PROFILE`, both key variables and both configuration paths pointed at nothing, and all five answered normally.

**Two verbs are not like that and this is the one place that is said.** `edullm run` and `edullm shell` start a machine of your own, which means they need an AWS session on your laptop as well, and the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) beside the AWS CLI. The session comes from the broker and from nowhere else.

```
sb-aws-creds login
```

There are no long-lived AWS keys in this account and creating one is refused, so that command, followed by the approval it opens in your browser, is the way. Without it both verbs stop before they start anything and say the same thing.

**Getting `sb-aws-creds` onto your laptop in the first place is the part that is not settled.** `npm view sb-aws-creds` answered 404 on 2026-08-06, so it is not on the public registry and no install line is printed here rather than one that fails. If you do not already have it, file `edullm ask --kind access-request` and it will be answered with whatever the route turns out to be. **Nothing on the submission path waits on that.** [A machine of your own](#a-machine-of-your-own) is what the two verbs are and are not for.

Then, from a checkout of the repository you work in:

```
edullm check --experiment onboarding --dataset none --team scratch
```

**`check` is the half that happens on your laptop, and it is the one to lean on.** It writes a first `.edullm/run.yaml` if the repository has none, then prices what you are about to submit and lists every refusal. They are the same refusals admission makes, decided against the reviewed configuration your install carries. It opens no connection and answers in about a fifth of a second, so it is a thing to run while you are still editing rather than once at the end. It works on a login node with no egress.

This is what it printed from a clone of OLMo-core on 2026-08-06, with the first line naming the configuration directory cut.

```
manifest
  repository        OLMo-core
  commit            9ea6d144f89c
  image             resolved at submit, from the commit above
  workload          olmo-core-check      1h ceiling, 1 attempt, no checkpoint contract
  compute           gpu-1xt4             g4dn.xlarge, 1 GPU, $0.526/hour
  dataset           none
  team              scratch              named on the command line
  experiment        onboarding
  wandb project     scratch

worst case  $0.53
  $0.526/hour x 1 node x 1h x 1 attempt x 1 cell
  A ceiling rather than an estimate, and what routes the run. Lowering --hours
  is what moves a run under the automatic bound.

what it has taken
  5 succeeded runs of this workload, on this machine, on this dataset took a
  median of 24s, between 0s and 25s. 3 more runs failed and are not in that
  figure. Measured on 2026-08-06 over 201 run(s) recorded by this platform.

approval
  automatic. One cell, under $500, so nobody releases this.

not checked here, because both need the container registry
  no_published_image
    Whether this commit published an image. A push to edullm/** builds one,
    and the submission workflow holds the credential that asks the registry.
  image_scan_findings_unreviewed
    Whether the registry's scan findings for that image have been read.
    Decided where the findings are, and admission re-derives it after
    approval.

no refusals. edullm submit will dispatch this.
```

**Read the ceiling and the approval line out of your own run rather than out of the block above.** Both come from reviewed configuration and both have moved this week. `what it has taken` moves faster still, because it is measured over every run the platform has recorded, and it is the only figure here that is an observation rather than a bound.

Nine verbs. `edullm` on its own prints the list and `edullm <verb> --help` prints what one takes.

| Verb | What it does |
| --- | --- |
| `edullm check` | Prices a submission here and lists every refusal. Dispatches nothing |
| `edullm submit` | Dispatches it. `--help` says it waits for the run id and it does not, see below |
| `edullm status` | Your recent runs. Give it a run id for one of them |
| `edullm logs <run-id>` | The last lines that run printed |
| `edullm cancel <run-id> --reason ...` | Stops it. The reason is required, and is recorded |
| `edullm add` | Teaches the platform a repository, dataset, shape, model or person. Opens a configuration pull request |
| `edullm ask` | Files one issue a person answers. It grants nothing itself |
| `edullm run --project p --compute c -- <command>` | A machine of your own, this directory on it, output streamed back. Needs an AWS session |
| `edullm shell --project p --compute c` | A terminal on that same machine, or a notebook with `--notebook`. Needs an AWS session |

The flags are the fields the form asks for, and `check` and `submit` take the same ones. The command, the workload profile and a suggested machine are properties of the code, so they live in `.edullm/run.yaml` and travel with it in git. What a run costs today is typed on the command line, because one commit run by two people belongs to two teams.

**`edullm submit` returns before your run has an id.** Its own `--help` says it waits for the one the compile job mints unless `--no-wait` says otherwise. Measured on 2026-08-06 it came back in 7.7 seconds with a workflow link and a line saying the id is still compiling. The compile job takes about two minutes and `edullm status` carries the id after that.

**`edullm status` with a run id reaches AWS, and without one it does not.** The bare form reads GitHub, takes about ten seconds and costs nothing, so you may call it in a loop. Naming a run, or `logs`, or `cancel`, dispatches `cancel-run.yml`, which holds the only identity allowed to read a Batch job, and then waits for a runner. Give those one to three minutes.

**The bare form never says a run finished.** It reports `DISPATCHED` while the submission workflow is running and `SUBMITTED` once that workflow succeeded, and neither moves again however the job ends. A run that succeeded an hour ago still reads `SUBMITTED`. To learn what a job did you have to name it, which is the form that spends a runner.

### Setting a team once

You only have to pass `--team` when the roster cannot answer for you. It answers when `config/organization.yaml` puts you on exactly one declared group. It cannot when you are on two, and then every `check` and every `submit` is refused with `team_is_ambiguous` until you name one.

Write the group you usually charge to into one file and you stop being asked.

```
mkdir -p ~/.config/edullm && echo pre-training > ~/.config/edullm/team
```

The file holds one team id on its first line and nothing else. There is no command that writes it, because there is nothing to write but the word. It goes in the same place on macOS, Linux and WSL, or under `XDG_CONFIG_HOME` if you have set one. On native Windows that path is `%USERPROFILE%\.config\edullm\team`, which PowerShell will create for you with `New-Item -ItemType Directory -Force $HOME\.config\edullm`.

This is yours and it is local. It is not reviewed configuration, it is read by nothing but your own `edullm`, and it does not travel with your code. `edullm check` prints the team it used and names this file on the same line, so a transcript still says where the team came from, and `--team` on the command line beats it for one run.

**It fills the field in and it gets you nothing.** A default naming a group the roster does not put you on is refused exactly as typing that group would be, and one naming a group that does not exist is refused as an unregistered team. It saves keystrokes and changes no outcome.

## A machine of your own

`edullm run` and `edullm shell` are the other half of the tool and they are not the submission path. `run` copies the directory you are standing in onto an EC2 instance of your own and streams back the output of the command after a bare `--`. `shell` opens a terminal on that same machine, or a Jupyter notebook on it with `--notebook`. Both verbs call it **the lane**, which is the word their help text and their refusals use, and it means a machine you hold rather than a job the platform queues for you.

**Nothing off the lane is a run anybody can cite, and that is the distinction this whole platform is built to draw.** A submitted run is checked against the registry, priced, released and written to a lineage record before it starts, and one run id then names the job, its outputs, its checkpoints and its Weights and Biases run, never rewritten. That is what lets a number in a paper be traced back to the commit, the corpus and the machine that produced it. The lane does none of it. What comes off the lane is a thing you saw. Use it to find out whether a script runs at all, then submit the version that has to count.

**The lane needs an AWS session, and `check` and `submit` do not.** With no credential it refuses before any machine is asked for.

```
AWS would not say who you are, so no machine was asked for. The lane needs an
AWS session the way the recorded path needs gh: log in the way you normally do
and run this again.
```

`edullm shell` needs one more thing, the AWS Session Manager plugin on your own laptop, and refuses with `session_plugin_missing` when it is not on `PATH`.

**How the sixteen of us holding no AWS role get a session is not settled yet.** There is a credential broker in the rollout notes whose install command does not resolve, so no command for it is printed here rather than one that fails. File `edullm ask --kind access-request` and it will be answered with whatever the route turns out to be. **Nothing on the submission path waits on this.** You can produce a citable run today with `gh auth login` and nothing else.

## Keeping edullm current

Run the install line again. `--force` makes it idempotent, so the one line installs, upgrades and repairs.

**`uv tool upgrade` is not the shortcut it looks like.** It follows the git ref the install named, so from the bare URL above it re-resolves the default branch and does upgrade, but from a release note's line, which pins that release's tag, it answers `Nothing to upgrade` and exits 0 however far behind you are, and `--reinstall` rebuilds the same commit rather than changing that. Both installs are in the field, so the line above is the instruction here: it is the upgrade for either, and it costs a few seconds on an install that was already current.

The reviewed configuration travels inside the install, which is what stops a config change bricking every `edullm` in the field, and means an old install is checking against an old copy. `edullm submit` asks for the current release before it dispatches and says so if yours is not it. It never refuses on that: a release is cut most days, so being a little behind is the normal state, and admission re-derives every verdict from inside AWS regardless.

`edullm --version` prints the version and the commit it was built from. That is the pair worth quoting when a refusal looks wrong.
