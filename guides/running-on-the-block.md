# Running a job on the capacity block

This is the procedure, in the order the questions actually come up. It is for a researcher who
wants to put work on the block and holds no AWS role, which is roughly fifteen of the thirty-five
of us. Everything below is a workflow dispatch and a browser; nothing on this page needs a
credential you do not already have.

[Using the capacity block](the-capacity-block.md) is the other half and is worth reading first,
once. It answers whether your repository can run here at all, what the image carries, and why
your branch's `src/` is probably not the library your process will import. This page assumes you
have decided to run and want to know what to type.

**Nothing you do here is a run anybody can cite.** No admission record, no approval, no lineage
entry, no run id. Anything that has to be reproducible goes through `edullm submit` on the
platform in `us-east-1`, which is untouched and keeps working all week.

## Two things to know before the first dispatch

**Dispatch from `main`, always.** Every one of these workflows assumes an AWS role whose trust
policy pins the workflow's own path at `refs/heads/main`. A dispatch from a branch does not fail
in an interesting way and does not fail late -- it dies at `configure-aws-credentials` with a
message about a subject claim, which reads like a broken workflow rather than like a wrong ref.
In the Actions UI the branch selector defaults to `main`; from the command line pass `--ref main`
even when you are standing on a branch.

**Dispatch one fleet-lane workflow at a time and let it finish.** `block-run-distributed.yml`
and `block-launch-fleet.yml` share the concurrency group `capacity-block-fleet`, which serialises
them on purpose -- a multi-node claim must not interleave with another one. What that group also
does, and what nobody expects, is discard a *queued* run when a third dispatch arrives: GitHub
keeps one running and one waiting, and the newest arrival evicts the waiting one. Three dry runs
fired in a row on 2026-08-10 produced two results and one `cancelled` with no explanation
anywhere in its log. The single-node workflow is grouped per node and does not behave this way;
eight people starting eight single-node runs at once is the intended Saturday.

## 1. How do I see what is free

Dispatch **Block: which node is free**. It takes no inputs worth filling in, reads every machine
directly rather than a sheet, and prints a table into the job summary.

```bash
gh workflow run block-status.yml --ref main -R edu-llm/platform
```

Wait about thirty seconds, then read the summary of the run it started. One line per node:

| Row | What it means | What to do |
| --- | --- | --- |
| `IDLE` | Nobody holds it and no cards are in use | Take it |
| `n/8 GPUs busy` with a name and a duration | Somebody is running on it | Take another, or talk to them |
| `STALE CLAIM` | A claim is there, its container has exited, no cards are in use | Nothing is running. See [section 8](#8-it-says-the-node-is-busy-and-nothing-is-running) |
| `NOT READY` | The machine is up and its bootstrap never finished | It can run nothing. Take another and say so in the channel |
| `UNREACHABLE` | Systems Manager could not reach it | Nothing can say whether it is busy. A fleet problem rather than yours |

**The fleet is however many nodes somebody launched, and that is not always eight.** This is the
single most common wrong assumption about the block, and it is wrong in the expensive direction:
a plan that needs four machines is not a plan the fleet can necessarily serve. The reading above
is the only thing that knows. On 2026-08-10 at 18:00 UTC it printed exactly one line --

```
node 1  i-0237f01429eec77ec  0/8 GPUs busy   philote-dev-throughput  mfu-smoke-1n-c   running 0h19m
```

-- because the fleet running at that moment was a single node, relaunched thirty-eight minutes
earlier, and every multi-node dispatch that afternoon was refused for that reason and not for any
reason to do with the dispatch. Count the rows before you decide how many nodes to ask for.

## 2. How do I claim what I need

You do not claim separately. **Starting a run is what takes the claim**, and the claim is a file
on the machine rather than a row on a sheet -- so it is the same lock whether the other person
arrived through a workflow, through a shell, or through the other workflow.

The shared sheet is still worth keeping. It is how several people plan a weekend, and it records
intent, which the machines cannot. It is not a lock and cannot stop two dispatches ninety seconds
apart; on these cards that collision does not queue, it produces two runs that each allocate,
fight, and die of memory some minutes later having cost each other a slot.

Which workflow takes the claim depends on how many machines you want, and that is the whole of the
difference between them:

| Machines | Workflow | What it does with them |
| --- | --- | --- |
| One | **Block: start a run on a node** | One job on one machine's cards |
| One or more | **Block: start one run across several nodes** | *One* job spanning all of them, with a `torchrun` rendezvous |

Running the single-node workflow four times gives you four independent jobs, not one job on four
machines. If your training run needs to see thirty-two cards as one world, you want the second
one.

**The multi-node workflow accepts `node_count=1`, and that is the recommendation for anything
that wants every card on a machine.** It is not a special case there -- it builds the same
rendezvous, elects the single machine as its own host, and starts eight ranks. What it buys you
is that the button, the fields and the command do not change when you go from one machine to
eight: you smoke-test on one, change `node_count`, and dispatch the same line. Reach for **Block:
start a run on a node** when the job is single-process, or when you want the simpler container.

The multi-node workflow takes every machine it needs in one go or takes none. A set that cannot
be assembled leaves nothing locked, so a refused dispatch costs you a minute and costs the fleet
nothing.

## 3. What exactly do I type

### Always dry-run the multi-node case first

`dry_run: true` reads the fleet, prints the plan and the exact command every rank would run, and
claims nothing. It costs about forty seconds. Do it once for any node count you have not used
before, then dispatch again with `dry_run: false`.

### One node

Fields that matter; the rest have defaults that are right.

| Field | What to put |
| --- | --- |
| `node` | A number from the status table that reads `IDLE` |
| `branch` | Your branch, pushed. Resolved against GitHub before the node is touched, so a typo costs nothing |
| `run_name` | Letters, digits, dot, dash, underscore. Put something of your own in it -- see [section 5](#5-where-does-my-output-go) |
| `repository` | `edu-llm/<yours>`. Defaults to `edu-llm/OLMo-core`; it is an ordinary field, change it |
| `command` | Your entrypoint and its arguments. Empty means "read `.edullm/run.yaml` from my branch", which only OLMo-core carries |
| `processes` | **`all` for a training run. `1` for anything single-process.** See below |

```bash
gh workflow run block-run.yml --ref main -R edu-llm/platform \
  -f node=1 \
  -f branch=my-branch \
  -f run_name=ana-mfu-smoke-1 \
  -f repository=edu-llm/OLMo-core \
  -f command='python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4 --dataset-id=regmix-10b-v1' \
  -f processes=all \
  -f wandb_project=capacity-block \
  -f region=us-east-2
```

**`processes` is the field that will cost you an afternoon if you skip past it.** The node runs
your command through a shell, and a shell starts one process. One process on a `p5.48xlarge` uses
one H100 and leaves seven idle, and nothing anywhere says so -- the run starts, the loss falls,
and the only symptom is a step time you have no baseline for on the first day. It is exactly how
the first attempts on this fleet died. `processes=all` puts `torchrun --standalone
--nproc-per-node 8` in front of your command for you, with the card count read off the machine
rather than assumed.

`auto` is the default and it refuses rather than guessing, because a bare command on an eight-card
node is either a training run that wants all eight or a tokenisation or an evaluation that wants
exactly one, and the dispatch cannot tell which. If you leave it at `auto` on a multi-card node
you will get:

```
launcher_refused:the command names no launcher and this node has 8 cards, so it would run one
process on one card and leave 7 idle. Say which you meant: processes=all for one process per
card, processes=1 to run it exactly as written.
```

That refusal happens before the node is touched, so it leaves nothing behind. Two cases answer
themselves and are not refused: a command that already contains its own `torchrun`, which is left
exactly as written, and a node with one card.

### Two nodes

```bash
gh workflow run block-run-distributed.yml --ref main -R edu-llm/platform \
  -f run_name=ana-two-node-smoke \
  -f branch=my-branch \
  -f repository=edu-llm/OLMo-core \
  -f command='python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4 --dataset-id=regmix-10b-v1' \
  -f node_count=2 \
  -f nodes= \
  -f expert_parallel= \
  -f mesh_flags=true \
  -f wandb_project=capacity-block \
  -f fabric=auto \
  -f dry_run=true \
  -f region=us-east-2
```

`node_count=2` takes the two lowest-numbered free machines. Read the plan, then send the same
line with `dry_run=false`. Changing `node_count` to `1` or to `8` is the only edit between the
three cases on this page.

**Turn `mesh_flags` off unless your entrypoint is the OLMo-core mixture-of-experts recipe.** Left
on, the dispatch appends `--moe-shard-degree` and `--moe-num-replicas` to your command, computed
from the machines it claimed. That is the point of it on `edullm/final-model`, whose entrypoint
takes both. On anything else argparse meets an unrecognised argument and every rank exits within
seconds of the containers coming up -- after the node set has been claimed and the machines paid
for. It is on by default because the flagship is what the block was bought for, and it is a
field rather than a guess because nothing here can read your argument parser.

A one-node dry run on 2026-08-10 printed this, which is what the plan looks like when the fleet
can serve it:

```
8 ranks: 1 replicas x 8 expert-parallel, 4 of 32 experts a rank
rendezvous 172.31.3.137:29400 on node 1
torchrun --nnodes=1 --nproc-per-node=8 --max-restarts=0 --rdzv-id=procedure-plan-1n
  --rdzv-backend=c10d --rdzv-endpoint=172.31.3.137:29400 --rdzv-conf=join_timeout=900
  --no-python python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4
  --dataset-id=regmix-10b-v1 --moe-shard-degree 8 --moe-num-replicas 1
```

Everything from `torchrun` to `--no-python` was added for you, and so were the last two flags.
Your `command` is the part in the middle.

**There is no `processes` field here and there must not be one.** The launcher is not optional on
this path and is not yours to write: `--nnodes`, `--nproc-per-node` and the rendezvous endpoint
all depend on which machines were claimed thirty seconds ago, so they are decided by the dispatch
and the same line goes to every node. Your `command` is the entrypoint and its arguments and
nothing else. A command that already names `torchrun` is refused rather than wrapped, because
wrapping it would start one launcher per card each starting one worker per card.

### Eight nodes

```bash
gh workflow run block-run-distributed.yml --ref main -R edu-llm/platform \
  -f run_name=ana-full-fleet \
  -f branch=my-branch \
  -f repository=edu-llm/OLMo-core \
  -f command='python .edullm/train_on_corpus.py --model-factory=olmoe_7b_32x4 --dataset-id=regmix-10b-v1' \
  -f node_count= \
  -f nodes=1,2,3,4,5,6,7,8 \
  -f expert_parallel= \
  -f mesh_flags=true \
  -f wandb_project=capacity-block \
  -f fabric=auto \
  -f dry_run=true \
  -f region=us-east-2
```

**Name the nodes or give a count, never both.** A list says which machines and a count says how
many, and honouring both would mean silently ignoring one -- which is always the one you cared
about. The form refuses if you fill in both or neither, before it touches anything.

Name them when it matters which. Node 8 is usually held back for the post-training and evaluation
lane, so `nodes=1,2,3,4,5,6,7` is the ordinary way to ask for the fleet; `node_count=7` would take
the seven lowest-numbered free ones and could quietly include node 8 if something else was busy.

A named node that is not free is a refusal rather than a substitution, and that is deliberate: if
you asked for 1 through 7 because 8 is reserved, you do not want 8 quietly used because 4 was
busy.

`expert_parallel` can stay empty. Left empty it picks the widest expert-parallel degree that still
fits inside one machine, which keeps the mixture-of-experts all-to-all on NVLink instead of on the
network between machines. Setting it wider than a node's card count is accepted by everything, is
not an error anywhere, and makes every step several times slower.

### What a refusal looks like, and what it costs

There are two different refusals here and telling them apart saves you asking the wrong person.
**The fleet is too small** reads as one line and names nobody:

```
distributed_launch_refused:the fleet has 1 nodes this run could take and it asked for 2
```

That is what a two-node dispatch answered at 18:29 UTC on 2026-08-10, with node 1 sitting idle:
nothing was wrong with the dispatch and nobody was in the way, there was simply one machine.
Only a fleet launch changes that answer, and a fleet launch is not yours to make.

**Somebody is in the way** adds a line per machine:

```
distributed_launch_refused:the fleet has 0 nodes this run could take and it asked for 2
distributed_launch_refused:node 1 is held by philote-dev-throughput for mfu-noGmm-1n
```

The named form answers the same way and names each machine:

```
distributed_launch_refused:node 2 is not a running instance in this fleet
distributed_launch_refused:node 3 is not a running instance in this fleet
...
```

**It names every blocker rather than the first**, so you fix them all and dispatch once more
rather than discovering them one dispatch at a time. Both of those are facts about the afternoon
rather than about the dispatch, and both are answered by the status table before you type
anything. A refused launch gives back every claim it took and removes every container it started,
so there is never anything to clean up first.

## 4. What goes in the command, and what must be left out

**Leave the launcher out** unless you are on one node and have decided to write your own. The
multi-node path adds it and refuses a command that already has one. The single-node path adds it
when you say `processes=all` and leaves your command alone when you say `processes=1`.

**The first word has to be a program, not a script and not a variable.** `python train.py` is
right; `train.py` is wrong even though it looks fine, because a fresh `git clone` gives a `.py`
file neither an executable bit nor a shebang, and `PYTHONPATH=/work/src python train.py` is wrong
on the wrapped path because there is no file whose name has an equals sign in it. Both are
refused at dispatch with a message saying so. Both are *fine* on `processes=1`, where a shell runs
your line and understands both.

**If your change is in library code rather than in an entrypoint, say so explicitly.** The image
already carries a full `olmo_core` on `PYTHONPATH`, so a script run from your clone imports the
image's copy and not yours. This is the single most expensive trap on the block and it is silent.
[Your library is the image's](the-capacity-block.md#your-library-is-the-images-not-your-branchs)
has the detail; the short version is that you need your clone in front of the path, and on the
wrapped path that has to go inside the entrypoint rather than in front of the command.

**Check what the image has before you rely on it.** torch, `olmo_core`, `edullm_data`, `boto3`,
`wandb`, `numpy`, `pandas`, `pyyaml`, `rich`, `safetensors`, and a compiler. No `git`, no `nvcc`,
no flash-attn, no vLLM, no DeepSpeed, no `transformers`, no `datasets`. A `pip install` in front
of your command works and happens again on every single dispatch, so a twenty-minute install is
twenty minutes off every iteration.

## 5. Where does my output go

The container is handed these and builds nothing itself:

| | |
| --- | --- |
| `$EDULLM_OUTPUT_PREFIX` | `s3://edullm-block-outputs-us-east-2/block/<reservation>/node-<n>/<run>/` |
| `$EDULLM_CHECKPOINT_DIR` | the same, with `checkpoints/` |
| the log | the same, with `log/train.log` |
| `$EDULLM_DATA_BUCKET` | `edullm-data-us-east-2`, the corpus mirror, readable and not writable |

Write your checkpoints to `$EDULLM_CHECKPOINT_DIR` and everything else under
`$EDULLM_OUTPUT_PREFIX`. A multi-node run gets **one** prefix for the whole job, built from the
elected node, because a distributed checkpoint is one directory that every rank contributes to.

**Put something of your own in the run name.** The S3 prefix carries the node number so two nodes
cannot overwrite each other's files. Weights and Biases does not: two people using `eval-1` on two
machines write into one W&B run and produce a chart that is the interleaving of two jobs.

## 6. How do I watch it

**Weights and Biases is the surface if you hold no AWS credential**, and the dispatch summary
prints the link. It works for everybody and it is the one to have open.

For the log, dispatch **Block: read a run's log**, which tails it into a job summary and needs
nothing installed:

```bash
gh workflow run block-logs.yml --ref main -R edu-llm/platform \
  -f node=1 -f run_name=ana-mfu-smoke-1 -f lines=200 -f region=us-east-2
```

Each node copies its logs to S3 once a minute, so a log is readable from outside the machine
within about that. On a multi-node run, rank 0's log is the one to read; the others are the same
job seen from another machine.

The dispatch summary itself answers the first question you will have, which is whether the thing
started at all: it prints the instance, the commit that was actually cloned, and the first forty
lines the run printed, read back about a minute after the container came up. A run that died on a
missing import shows it there.

## 7. How do I stop it

**Be honest with yourself about whether you need to.** A run you leave to finish costs the block
nothing extra; the machine is paid for either way and the reclaim is not negotiable.

If you hold an AWS role, from a shell on the machine:

```bash
aws ssm start-session --target <instance id> --region us-east-2 --profile sbsandbox
sudo docker stop edullm-<run name>
sudo edullm-node release
```

`release` refuses while a container is still up, which is the point of it -- releasing a claim out
from under a running job is the one way to make the lock lie.

**If you hold no AWS role, there is no way to stop only your own run today, and you should ask
rather than improvise.** The one credential-free lever is `Block: drain the fleet before AWS takes
it back` with `stop_runs: true`, and it is fleet-wide: it asks *every* claimed run on every node
to stop, not yours. Do not reach for it to end one job. Ask in the channel for somebody with a
role, which takes a minute and ends the right process.

## 8. It says the node is busy and nothing is running

This is the most common confusing state on the block and it has a name now. **Nothing on a node
removes a claim when a container exits**, so a run that finished hours ago leaves the machine
reading as held by somebody who has gone home.

The status table calls it out:

```
node 3  i-0abc...  STALE CLAIM  ana / mfu-smoke exited claimed 2h00m ago; `edullm-node release`
```

and a dispatch onto that machine refuses with `node_claim_is_stale` rather than `node_is_busy`,
which are different situations and used to print the same words.

What to do, in order of preference:

1. **Take another node** if there is an `IDLE` one. Always the cheapest answer.
2. **Ask somebody with a role to run `edullm-node release`** on the machine. One command, seconds.
3. `take_the_node_anyway`, **and only if the refusal you got used the exact words
   `node_claim_is_stale`**. In that one case the reading has established that the container is
   gone and no cards are in use, so there is no other run to fight and what you overwrite is a
   claim nobody is using. If it said `node_is_busy`, it is busy: go back to 1 or 2.

Point 3 is a narrow exception to the rule in the next section, and the words are the whole of the
exception. "It looked stale to me" is how somebody ends a colleague's training run.

**Zero cards in use is not on its own evidence that a claim is stale, and you should not treat it
as such.** A run that is cloning, importing torch, sharding a corpus or simply between steps holds
no cards while being entirely alive, and the claim is taken *before* the clone precisely so that
two dispatches seconds apart cannot both proceed. The container is what settles it, which is why
the reading asks about the container and why the refusal names it.

This is not a theoretical distinction. Node 1 read `0/8 GPUs busy` under a claim at 18:00 UTC on
2026-08-10 and read `8/8 GPUs busy` under the same claim nineteen minutes later: it was one run,
starting up, and anything that had called it abandoned would have put a second job on a machine
that was about to use every card on it.

## 9. What you must not do

**Do not pass `take_the_node_anyway` to get past `node_is_busy`.** It means what it says: the
other run keeps its cards and yours fights it for memory, and on these machines that is two dead
runs rather than one. The single exception is a refusal that specifically said
`node_claim_is_stale`, above. An agent came within a step of cancelling somebody's live training
run on this fleet on 2026-08-10 by treating a refusal as stale without checking.

**Do not run `block-launch-fleet.yml`.** It starts machines against a purchase that cannot be
refunded, it is guarded for that reason, and a fleet that is already up does not need it.

**Do not stop or terminate an instance.** `/scratch` is a RAID0 stripe over local NVMe and does
not survive a stop, so stopping a node destroys the tree, the logs and anything not already in S3
-- and gives back nothing, because the window is paid for either way. If a machine is wedged, say
so in the channel.

**Do not call AWS directly to work around a refusal**, and do not edit a claim file by hand. Every
credential in this lane lives in a workflow. A script that reaches past them either fails, for the
people who hold no role, or succeeds and leaves no record, for the people who do.

**Do not re-use a run name for a job that is still running.** Two live jobs of one name write into
one W&B run and one rendezvous id. Re-using a name whose job has *finished* is fine and expected;
on a fleet launched since this page was written, the exited container that used to block it is
cleared for you, and on an older one you will meet a refusal naming the container instead.

## What this lane still cannot do

Written down so that nobody spends an afternoon discovering it.

- **A researcher with no AWS role cannot clear a stale claim themselves.** They can see it, and
  the refusal names the cure, and the cure is a command on a machine they cannot open. Asking
  somebody works and is what the procedure says to do; a workflow that ran `edullm-node release`
  and nothing else would close this properly.
- **There is no way to stop one run without a role.** `block-drain.yml`'s `stop_runs` is
  fleet-wide.
- **The node-side fixes only reach a node when a fleet is launched.** The helper is written into
  each machine's user-data while it boots, so a change to it does nothing to a machine that is
  already up. The atomic claim and the cleared container name apply to fleets launched after they
  merged; the stale-claim reading and the `processes` field are in the workflows and apply
  immediately.
- **`edullm-node run` typed into a shell still starts one process.** The `processes` decision is
  made in the workflow, because the node's helper is EC2 user-data with a hard 16,384-byte limit
  and under a kilobyte of it left. People with a shell keep composing their own `torchrun`.
- **A second image is not available and is not coming this week.** The node's role permits one ECR
  repository and refuses every other pull. See
  [what cannot be fixed](the-capacity-block.md#what-cannot-be-fixed-before-saturday).
