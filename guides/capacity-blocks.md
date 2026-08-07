# Capacity blocks

Some machines this platform prices cannot be obtained. EC2 sells accelerated capacity to whoever asks first, and for the P family this account has asked thousands of times and been refused. A capacity block is the way round that: a dated window during which the machines are yours because they were paid for in advance.

This guide is for both sides of that. A researcher needs [the modifications to make](#the-modifications-to-make-before-the-window-opens) and everything after it. Whoever buys the block needs all of it, and hands that section over when the purchase is made.

## The thing to understand before anything else

**A capacity block cannot be cancelled, and the money is spent whether or not a single job runs.** The reservation fee is charged upfront, refunds do not exist, and the window opens on its calendar date regardless of whether anybody is ready for it.

That one property is what shapes everything below. Every step in this guide exists to move a decision or a failure to *before* the purchase, where it costs nothing, rather than after it, where it costs the whole block.

## What you can buy

Capacity blocks cover the P and Trainium families. These are the ones sold in `us-east-1`, which is where every queue on this platform lives.

| Block | Cards | GPU memory | Published rate | About a day | Profile |
| --- | --- | --- | --- | --- | --- |
| `p5.4xlarge` | 1 × H100 | 80 GB | $5.191/hr | ~$125 | `gpu-1xh100` |
| `p4d.24xlarge` | 8 × A100 40GB | 320 GB | $11.80/hr | ~$283 | `gpu-8xa100` |
| `p4de.24xlarge` | 8 × A100 80GB | 640 GB | $17.712/hr | ~$425 | `gpu-8xa100-80gb` |
| `p5.48xlarge` | 8 × H100 | 640 GB | $41.528/hr | ~$997 | `gpu-8xh100` |
| `p5en.48xlarge` | 8 × H200 | 1128 GB | $54.920/hr | ~$1,318 | `gpu-8xh200` |
| `p6-b200.48xlarge` | 8 × B200 | 1432 GB | $98.84/hr | ~$2,372 | `gpu-8xb200` |
| `p6-b300.48xlarge` | 8 × B300 | 2144 GB | $112.32/hr | ~$2,696 | `gpu-8xb300` |

Every row has a profile behind it as of 7 August 2026. **None of them has a queue behind it**, which is a different thing: all seven shapes above `gpu-8xa100` carry `provisioned: false`, so `edullm check` refuses them with `unprovisioned_compute_profile` until a block is actually bought and its stack deployed. A profile means the platform knows the machine's size, rate and memory ceiling. It does not mean anything can run there yet.

The rates are AWS's published effective hourly rates and they move with supply and demand. **The number that matters is the one on the offering**, which you see before you commit. Read that rather than this table.

`p6-b200`'s GPU memory is quoted two ways by AWS itself — 1,432 GB in the instance-type tables and 1,440 GB in the launch announcement. They describe the same silicon in different units: the card reports 183,359 MiB, which is 179 GiB, and eight of those is either 1,432 (counting GiB) or 1,440 (counting decimal bytes at 180 GB a card). `config/accelerators.yaml` carries the exact per-device figure. Size a run against that rather than against either headline.

Three families are deliberately absent. `p5e.48xlarge` is not sold in N. Virginia — AWS offers it in Ohio, Oregon, N. California and overseas, and `describe-instance-type-offerings` returns nothing for it in any `us-east-1` zone. The `p6e-gb200` UltraServers are sold only in the US East (Dallas) Local Zone, which the account would have to opt into. `trn1.32xlarge` is cheap but it is Trainium: it needs the Neuron SDK, and the training image is CUDA and torch, so nothing here would run on it.

## The rules AWS imposes

- Durations are **whole days**: one to fourteen in one-day steps, then multiples of seven up to 182. There is no such thing as a six-hour block.
- You can book a start up to **eight weeks ahead**, and in practice the earliest offering is usually two to four weeks out. Plan against that lead time.
- One to 64 instances per block, up to 256 across several.
- **Every block ends at 11:30 UTC**, whatever time it starts. When you ask for 24 hours you are offered several blocks whose real durations bracket that, one slightly under and one slightly over, and you pay for the one you take.
- Searching across a **wide date range** returns the lowest-priced offering in it. A flexible deadline is worth real money.
- AWS begins **terminating your instances 30 minutes before the end** to clean up for the next customer, and emits an EventBridge event ten minutes before that starts. Your usable window is half an hour shorter than the number you bought.

## Choosing which one

The researcher's peak GPU memory picks the row and nothing else does. Under 320 GB, you do not need a block at all — `gpu-8xa100` is provisioned today and jobs on it start after a wait measured in tens of minutes. Up to 640 GB is `p4de.24xlarge` rather than `p5.48xlarge`: same 640 GB, 43% of the price, older cards. Up to 1128 GB is `p5en.48xlarge`. Above that you are into Blackwell.

Buy the smallest block that fits. The gap between rows is not marginal: 640 GB costs about a sixth of 1432 GB per day.

Two rows are easy to reach past and should not be. `p4de.24xlarge` is the cheapest 640 GB on the menu and the row most people skip on the way to `p5.48xlarge`, which costs 2.3 times as much for the same memory. `p5en.48xlarge` has more memory per card than `p5.48xlarge` — 141 GiB against 80 — for a third more money, so if the constraint is fitting a model rather than a specific generation of card it is the better buy of the two.

`gpu-8xb300` is the one to be careful with, and not because of the price. Its container memory ceiling has never been measured: nothing has run a `p6-b300.48xlarge` here, and the figure the platform asks for is a deliberate under-estimate derived from the lowest fraction any host in this account has ever registered. A first run there gets less memory than the machine has. Correcting it needs one CloudTrail lookup after the first launch, and `src/edullm_platform/execution.py` says which.

Both Blackwell rows carry a software risk that is not visible in this table. AWS publishes CUDA 12.8 and driver R570 as the minimum for `p6-b200`. The training image's CUDA base clears the first; nothing has confirmed the driver on the actual silicon, because the driver version this platform knows was read off an A10G. A block is the first thing that would settle it, which is an argument for buying a short one first.

## The modifications to make before the window opens

This is the section to hand over when a block is bought, and it is the step most likely to be skipped, because nothing refuses a submission for getting it wrong. `edullm check` reads a command and a spec. It cannot tell that a batch size was chosen for a different card, and it cannot tell that an image will not initialise a driver it has never met. Every item below is discovered on the machine unless it is done before the window.

**All of it has to be finished before the start date, and two items need a commit and an image build.** The image is built from a commit, so a change to your own repository is not in effect until the build workflow has published a digest for it. Leave time for that: a build is minutes, but a build that fails and needs a second attempt is an afternoon, and the window opens whether or not the image is ready.

### Re-size the run for the card, do not scale it up on the day

A configuration that has run is worth more than a configuration that fits on paper. Bring the batch size, sequence length and parallelism degrees that actually ran on the shape you developed on, submit that, and scale once it is running and you can see memory in W&B. The gap between cards is large enough that guessing wastes real money: going from 8 × A100 80GB to 8 × B300 is 640 GB of device memory to 2144 GB, so a batch size tuned for the first leaves most of the second idle, and one tuned optimistically for the second does not start.

Two model-config changes tend to be needed alongside the batch size and are easy to forget, because neither is a memory error when it goes wrong. Tensor and pipeline parallel degrees have to divide the eight cards you now have rather than the four you tested on, and a launcher that starts a number of processes different from the number of devices is the one thing `check` does catch — `process_per_device`.

### Host memory is not device memory, and the container gets less than the instance

The container asks for a fixed slice of host RAM, and it is deliberately under what the machine advertises: an ECS host registers less memory than EC2 advertises for it, and a container asking above what the host registered is not slow, it is unplaceable. Batch answers `MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT` on the job without launching anything, so a queue that never scales is the symptom.

| Shape | Device memory | Instance memory | Your container gets |
| --- | --- | --- | --- |
| `gpu-8xa100-80gb` | 640 GB | 1152 GiB | 1092 GiB |
| `gpu-8xh200` | 1128 GB | 2048 GiB | 1936 GiB |
| `gpu-8xb200` | 1432 GB | 2048 GiB | 1936 GiB |
| `gpu-8xb300` | 2144 GB | 4096 GiB | 3787 GiB |

**`gpu-8xb300` is the row to read twice.** A researcher sizing a dataloader or an offload buffer to the 4 TiB the machine advertises will not get it, and will not get 4 TiB minus a small allowance either — they get about 3787 GiB, some 309 GiB short. That figure is not a measurement. No `p6-b300.48xlarge` has ever started in this account, so it is a deliberate lower bound: the advertised memory times the smallest fraction any host here has ever registered, chosen to be certainly under whatever the real host publishes. It leaves memory on the table on purpose, because overshooting costs a job that will not place on a machine already being billed under a block that cannot be cancelled. `src/edullm_platform/execution.py` carries the arithmetic and the CloudTrail lookup that corrects it after the first launch, which costs nothing and needs no probe.

### Blackwell needs CUDA 12.8 and driver R570, and that may be a change to your repository

`gpu-8xb200` and `gpu-8xb300` are a new architecture rather than larger cards. AWS publishes CUDA 12.8 and driver R570 as the minimum for `p6-b200`. The training image's CUDA base clears the first on paper. Nothing has confirmed the second on the silicon, because the driver version this platform knows about was read off an A10G.

What that means for you, in order:

1. **Check your own pins.** A repository pinning `torch` to a build compiled against CUDA 12.1, or pinning a `flash-attn` or `transformer-engine` wheel built for Hopper, will import and then fail at the first kernel launch on a B200. Blackwell needs `sm_100`; a wheel built for `sm_90` and no PTX fallback has nothing to run.
2. **If a pin has to move, that is a commit and an image build**, and both happen before the window rather than during it.
3. **Buy a short rehearsal block first where you can.** A one-day `p5.4xlarge` costs about $125, which is roughly an hour of a B300, and it is the only thing that turns "the image probably works on that card" into a fact. This is the argument for a cheap block a week ahead of the expensive one.

`infra/batch-compute-gpu-shapes.yaml` excludes `p6-b200.48xlarge` from the H100 environment's fallback list for exactly this reason, so the platform is not quietly assuming it works either.

## What the researcher does

**Nothing on a real machine, and nothing that costs money.** `edullm check` runs offline and answers in a fraction of a second, so the loop is edit, check, edit, check, until it is clean.

Point it at the profile the block will provide:

```bash
edullm check --json --compute gpu-8xh100 --experiment <slug> --dataset <release-or-none>
```

Exit 0 means it stands. Exit 1 means read `refusals` and **match on `code`** — the prose beside it gets reworded. These are the ones that matter here, because each is a mistake that would otherwise be discovered on a machine you are already paying for.

| Code | What it means on a block |
| --- | --- |
| `process_per_device` | Your launcher starts a different number of processes from the number of cards. On an eight-card block this is seven idle GPUs at full rate |
| `checkpoint_path_not_in_command` | Your command never expands `$EDULLM_CHECKPOINT_DIR`, so a crash throws away everything up to it. The window does not stop for you |
| `bfloat16_not_in_the_hardware` | The card cannot do the format the command asks for |
| `unprovisioned_compute_profile` | The shape has no queue behind it. Before the block is wired up this is the expected answer, and it clears when the block is deployed |
| `unregistered_repository` | The platform does not carry this codebase at all. That is a separate job and it has to happen first |

Two things `check` cannot see, and both are yours to get right by reading rather than by running.

**A dtype set in code is invisible to it.** The bfloat16 guard reads the text of your command, so a trainer that fixes its precision in Python carries nothing for the guard to find. Write the dtype into the command so the check can see it.

**Nothing here predicts memory.** `check` prices the machine; it does not know what your model will allocate on it. The peak figure you put on the form is yours to measure and to be honest about, because it is what the block is bought against.

When the check is clean, file the form.

## Filing the form

Use the [ask](https://github.com/edu-llm/platform/issues/new?template=ask.yml) form and pick `capacity-block` as the kind, or run `edullm ask --kind capacity-block` from a terminal, which attaches your `edullm` version and which reviewed configuration you were checking against.

Four things decide the purchase, and the terminal will not file the ask without them.

```bash
edullm ask --kind capacity-block \
  --title "a b200 block for the long-context sweep" \
  --detail "The sweep needs a card the on-demand fleet does not carry." \
  --peak-gpu-memory "just over 1 TiB, from a 70B model at 32k context" \
  --hours-needed "18, from a two-hour run on an eighth of the tokens" \
  --needed-by "any time before 15 September" \
  --resume-tested tested
```

`--resume-tested` takes `tested`, `writes-only` or `none`, which are the browser form's three options in shorter words. Anything missing comes back as `capacity_block_ask_is_incomplete` naming all of them at once, and nothing is filed.

**The browser form marks the same four optional and that is not an inconsistency.** It is one triage form serving five kinds and GitHub cannot make a field conditional on a dropdown, so marking them required there would stop somebody reporting a broken run. A terminal knows the kind before it validates anything. Either door is fine; the terminal is the one that will not let you file an ask nobody can price.

An estimate you can state the basis of is a fine answer. An absent one is not, because the ask is arithmetic rather than judgement: peak memory picks the machine, hours picks how many whole days get bought, the date decides whether any offering can meet it, and a tested resume decides whether a crash costs an hour or the window. Paste your clean `edullm check --json` output too.

**The form grants nothing and reserves nothing.** Somebody reads it and buys a block, and that takes weeks rather than hours because the lead time is what it is.

## Buying and wiring it up

Search across the widest date range the deadline allows, and note three things off the offering you take: the price, the start time, and **the availability zone**. The zone is not optional detail — a block is a targeted reservation in one zone, and the compute environment has to be pinned to it.

Once purchased, the reservation appears in the EC2 console under **Capacity Reservations** with a reservation type of `capacity-block`. It moves through `payment-pending` to `scheduled` as the upfront charge clears, and to `active` when the window opens. If the payment cannot be processed it flips to `payment-failed` and the block is released, so check that it reached `scheduled` rather than assuming.

Then three changes, none of which can be skipped.

**Deploy the block stack** with the instance type, the reservation ID, the zone and the vCPU ceiling. This creates a launch template carrying `InstanceMarketOptions.MarketType: capacity-block` and the reservation ID, a compute environment pinned to that type and zone, and a queue.

**This is the step with no way around it.** A capacity block is a *targeted* reservation: AWS will not let anything consume it unless the launch explicitly names the reservation ID. Skip the launch template and Batch does not fail — it launches ordinary on-demand instances next to a block you have already paid for, and you are billed twice.

**Restore the profile's row** in `config/execution-targets.yaml` so submissions have somewhere to route, and **flip `provisioned` to `true`** in `config/workload-catalog.yaml`. Until both are done, `edullm check` refuses the shape with `unprovisioned_compute_profile`.

**Prove it before the window matters.** For any instance type nobody here has run, a cheap short block bought to start a week earlier is the only way to find out whether the image works on that card and whether your launch template really routes a job into a reservation. A `p5.4xlarge` day costs about as much as an hour of the machine it is de-risking.

## Submitting before the window opens

**Have the researcher submit the day before, not on the day.** Batch accepts jobs against a block that is not yet active: the job waits in `RUNNABLE` and is placed the moment capacity appears. Nobody has to be awake at 11:30 UTC.

The reason this matters is not tidiness. Every block-backed shape classifies as `exception`, whatever the run costs and however short it is, so `approval_class` comes back `exception` and a **platform admin** has to open the run page and release it before the job reaches Batch. That is a smaller set of people than a team lead, deliberately: the machine behind the shape has already been paid for and cannot be refunded, so the person who authorises spending it is the person who bought it. Submit after the window has opened and the block burns while somebody goes looking for an admin. Submit the day before and the approval, the image resolution and admission all happen on your time.

Read `approval_class` out of `edullm check --json` rather than assuming which way it lands. It is decided by the profile rather than by the price, so shrinking the run does not move it.

## Two things this does not do yet

Both are real gaps rather than decisions, and they are written down here so that the next person reaches for them rather than rediscovering them.

**Nothing joins an ask to a block to the runs that consumed it.** The ask is a GitHub issue, the block is a reservation id in the EC2 console, the stack is a CloudFormation stack, and the runs are lineage records. There is no field anywhere that connects the four, so "which runs used the block we bought for X" is answered by reading dates by hand. With one block a month that is tolerable; with five people holding blocks in one month it is not.

**Nothing reports what has been purchased.** `tools/read_launch_events.py` deliberately excludes `PurchaseCapacityBlock` from the events it reconciles, because it is an API call rather than an instance launch and would report as a mismatch on every run — the reasoning is in that file and it still holds. What follows from it is that a purchase reaches no report at all. A separate reader over `DescribeCapacityReservations` is what would answer "what are we currently paying for", and it does not exist.

## When the window closes

**Flip `provisioned` back to `false` and withdraw the `execution-targets.yaml` row.** This is the step that gets forgotten, and forgetting it is worse than it sounds: the flag is a permanent boolean describing a dated thing, so a shape left promoted after its block expires routes every later submitter to a compute environment that can never place them. What they see is a job sitting in `RUNNABLE` with no error against it, which is indistinguishable from being queued behind somebody else.

Put the revert on the calendar for the day the block ends, at the same time as you buy it.

## What none of this protects you from

Worth stating plainly, because a guide that reads as a guarantee is one nobody double-checks.

**A configuration that was never run.** A clean `check` on an eight-card shape says the launcher starts eight processes. It says nothing about whether the batch size somebody reaches for on 640 GB fits in 640 GB. Submit the configuration that has actually run, and scale it once it is running.

**A card nobody has used here.** The training image pins CUDA against a driver version read off an A10G and carried to other shapes by inference. It is a reasonable inference and it has not been confirmed on a P-family card. The cheap rehearsal block is the only thing that turns that from an assumption into a fact.

**A crash without a resume.** Writing checkpoints and resuming from them are different features, and only one of them is tested by writing one. A block does not pause while you fix a bug.
