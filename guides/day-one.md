# Day one

From nothing to a run that printed a number. Read this one and nothing else. Every command below was run on 2026-08-06 and what it printed is what is written here.

## Install the tool, then read the version back

Two commands, and the second one is a step rather than a note. Four seconds for the pair.

```
uv tool install --force git+https://github.com/edu-llm/platform
edullm --version
```

**That has to read 3.4.8 or higher.** Look at it. An install at 3.4.7 or below sends a command the platform cannot run, and you find out two minutes later.

Up to and including 3.4.7, `submit` rejoined your command with a plain space and lost the quoting on the way out. The day-one command below leaves as `bash -lc python .edullm/time_attention.py "$EDULLM_RUN_ID"`, the compile job splits it on the spaces, counts three words where `-lc` takes one, and refuses:

```
bash -lc reads exactly one word as the command, and this submission gives it 3. It would
run `python` alone and hand the rest to it as $0, $1, $2 -- which starts, costs an
instance, and exits without running your program. Quote the whole program: bash -lc
'python .edullm/time_attention.py $EDULLM_RUN_ID'
```

The count moves with your command. The advice on the last line is the trap: you did quote the whole program, the tool took the quotes off between your terminal and the form, and the line it hands back is the line you already had. Re-typing it more carefully is what everybody tries and it changes nothing. The platform was not down while this was happening, either. Six submissions went through in the same hours as seven of these.

**The fix ships in the tool rather than on the platform**, so 3.4.8 landing on `main` repaired no install but the ones made after it.

**Re-run that same line to upgrade.** `--force` makes it idempotent, so the one line installs, upgrades and repairs. Reach for it rather than `uv tool upgrade`, whose answer depends on how the tool was installed: from the bare URL above it re-resolves the default branch and does upgrade, but from a release note's line, which pins that release's tag, it answers `Nothing to upgrade` and exits 0 however far behind you are. Both installs are in the field and you are unlikely to remember which is yours. Naming the command rather than the package is no better: `uv tool upgrade edullm` errors and then suggests installing `edullm`, which fails too, because `edullm` is the command and `edullm-platform` is the package.

`--version` prints the commit next to the number. If that commit is not one on `main`, you are carrying a working copy somebody built rather than the released tool, and the `--force` line replaces it. Worth reading rather than assuming: the machine that proved the quoting fix was carrying a 3.5.0 built out of a local worktree, and it had to be replaced before it could test what a researcher would actually get.

If the shell cannot find `edullm` afterwards, compare `uv tool dir --bin` against `which -a edullm`, or `where.exe edullm` on Windows. They should be the same directory. Two lines means something else called `edullm` is on your path first.

## You do not need AWS credentials

Sixteen of the thirty-five of us hold no AWS role, and none of us needs one to submit a run. `check`, `submit`, `status`, `logs` and `cancel` drive `git` and `gh` and hold no cloud credential. All five were run on 2026-08-06 with `AWS_PROFILE`, both key variables and both configuration paths pointed at nothing, and all five answered normally. That was checked twice on the day, hours apart, because it is the claim most worth being wrong about.

What you do need is `gh auth login`. That is the whole of it. If `check` refuses with `submitter_unknown`, it could not read who you are and that is the command that fixes it.

Two verbs are the exception and they are not the submission path. `edullm run` and `edullm shell` start a machine of your own, and both need an AWS session. **Nothing off them is a run anybody can cite**, because nothing they do is checked against the registry, priced, released or written to a record, so what comes back is a thing you saw rather than a result. How the sixteen of us get a session is not settled, and the credential broker in the rollout notes has an install command that does not resolve, so no command for it is printed here. File `edullm ask --kind access-request` and read on. Nothing else on this page waits on the answer.

## Your first job

A real one. It times an attention block on one card across four sequence lengths and prints forward milliseconds, backward milliseconds and tokens per second for each.

```
git clone https://github.com/edu-llm/OLMo-core
cd OLMo-core
git checkout edullm/onboarding-smoke
edullm check --team scratch --experiment day-one --dataset none
```

Clone it in full. `git clone --depth 1` fetches only `main` and then the checkout has no branch of that name to switch to.

`check` reaches no network, answers in about a fifth of a second, and lists every refusal at once. Read the ceiling and the approval line out of what it prints rather than out of any document, because both live in reviewed configuration and move. On this branch it prints no refusals, a ceiling of well under a dollar on one T4, and an approval line saying nobody has to release it.

```
edullm submit --team scratch --experiment day-one --dataset none
```

**This one blocks, and that is the point of it.** It says so first, then dispatches, then waits for the compile job to mint your run id, printing a line a minute while it does and giving up after five. Measured on a real submission at 04:57 on 2026-08-06: **49 seconds**, ending with the id and the approval line.

```
run_019fd676-62f0-70bb-ae06-c35fcb715af7
released automatically. Nothing is waiting on a person.
```

Those two lines are the whole reason to wait. The id is how you ask about the run at all, and the second line is whether anybody has to release your work before it starts. Both used to be lost: `submit` returned in about eight seconds, before the compile job had finished, so every real submission left the submitter knowing neither. `--no-wait` skips the wait and prints the workflow link on its own, which is what you want in a script and not what you want the first time.

`submit` also reaches the network once to ask whether your install is the current release, and says so if it is not, with the exact line to run. It cannot stop a submission and a network failure skips it, so it is a courtesy rather than the version check at the top of this page.

## Reading it back

```
edullm status                       # your recent runs, and the run id
edullm status run_019fd5d7-915f     # one run, in full
edullm logs run_019fd5d7-915f       # the last lines it printed
```

`status` with no argument answers from GitHub in ten to twenty seconds. `status` on one run and `logs` reach AWS, which they do by dispatching a workflow and waiting for a runner. Both say so before they start waiting and both give up after eleven minutes; measured on 2026-08-06 they took 45 seconds and 58 seconds. A job sitting at `RUNNABLE` is waiting for a machine and is billing nothing. Do not cancel and resubmit it, because that puts it at the back of the queue.

Read `status` for what the job is doing and `logs` for what it printed. They answer different questions off the same run, and the report from `status` says at the bottom which verb holds your output.

This is what came back on 2026-08-06. Two minutes from `submit` to admitted, five waiting for a card, six seconds running.

```
card          Tesla T4
shape         batch 8, 12 heads of 64
median of     20 timed iterations after 3 warm-up
   seq   forward ms   backward ms   total ms       tok/s
   512        1.014         2.799      3.813   1,074,177
  1024        2.402         7.491      9.893     828,074
  2048        6.636        21.583     28.219     580,609
  4096       20.074        69.255     89.329     366,824
```

**Your milliseconds will not be these**, and nothing is wrong when they are not. It is a real measurement of a card somebody else was using an hour ago. Three runs of it across 2026-08-06 spread about six percent at the longest sequence, in both directions. What tells you it worked is the card, the shape, and four rows arriving.

## The notification, and how to find out without one

When a run ends the platform composes one line for `#edullm-runs`. It looks like this.

```
Aryan Verma · plan-b-phase0-100m-superbpe-eval · $0.02 spent, $2.01 authorised · ran 1m on gpu-1xa10g.
Aryan Verma · plan-b-phase0-100m-superbpe-eval · $0.70 spent, nothing produced · died at 42m on gpu-1xa10g, exit 1, whether a checkpoint survived is unknown.
```

**These are sent.** This section said nothing sent them and that the webhook had never been supplied, which was true for about four hours after it was written. The webhook was created by hand on 2026-08-05, points at `#edullm-runs`, and `infra/README.md` records it under "It already exists". Messages have been posted through the deployed function since. Join the channel.

**Do not poll `edullm status` for it, because the bare form never changes.** It reads GitHub, and what GitHub knows is whether your submission workflow succeeded rather than what your job then did. A run that finished an hour ago still reads `SUBMITTED`. What tells you is the channel, or `edullm status <run-id>`, which names the run to AWS and takes one to three minutes because it spends a runner to do it.

## Running your own code

Your commit has to have been built into an image before a run can name it, and a push to a branch under `edullm/` is what builds one. So the loop on a repository that has never been submitted from is five steps rather than two.

1. `edullm check`. On a registered repository with no spec it writes a first `.edullm/run.yaml` and tells you to commit it on a branch. It does not hold the file it just wrote against you. It used to, which was a loop with no way out, and that is fixed.
   Where it could not work out a value it writes something the next `check` refuses by name rather than something that looks right. In a clone with no `.edullm/` entry point to read, the command it writes earns `checkpoint_path_not_in_command`, and that refusal is a paragraph telling you what to pass and how to waive it. **That is the tool declining to guess, not a wall.** A guessed checkpoint path would cost a queue wait, somebody's approval and a run that exits zero having saved nothing.
2. Commit that file on a branch. `check` refuses with `commit_not_pushed` now, because no remote-tracking branch in your clone contains the commit, so nothing has built an image from it.
3. Push the branch under `edullm/`. The image build takes three to eight minutes.
4. `edullm check` again. It should print no refusals.
5. `edullm submit`.

**A refusal for a commit with no image is the platform working rather than the platform broken.** It caught somebody on 2026-08-06 who pushed and submitted in the same minute, while the build was still running. Wait out the three to eight minutes and submit again. If you pushed a while ago and still meet it, `git fetch` first, because the question is asked of the remote-tracking branches your clone holds rather than of GitHub.

Two checks are deferred to submit time and `check` names both, because they need the container registry and this tool holds no credential for it. A clean `check` is not a promise.

## Walls still standing

| Wall | The way round |
| --- | --- |
| No Windows machine has ever finished this. The install used to fail with `Filename too long` for any username over eight characters, and [#291](https://github.com/edu-llm/platform/pull/291) fixed that on 2026-08-06 along with the `gh` lookup, the spec's line endings and redirected output. All of it is untested on real Windows | Follow it anyway and say where it stopped. If the install still fails on a path, point `UV_CACHE_DIR` at something short such as `C:\uv` and try again |
| `edullm status` with no argument reads `SUBMITTED` for every run, whatever the job did. The state it shows is your submission workflow's rather than your job's, and it never moves again | Watch `#edullm-runs`, or name the run. `edullm status <run-id>` asks AWS and takes one to three minutes |
| The eval image carries no torch and no vLLM, so only the `mock` provider runs | Nothing yet. GPU evaluation through the platform is not available. eval-inference owns the choice |
| `gh` in a clone that has an `upstream` remote answers about the wrong repository, with no warning. In OLMo-core it reported no image build for a branch whose build had succeeded | Use `edullm`, which reads `origin`. Where you must use `gh`, pass `--repo edu-llm/<name>` |
| Roughly half of all runs fail, and about half of those failures print no cause | Nothing yet. Your first failure is probably not your fault. Bring the run id to an issue |
| A run that writes its checkpoints to `/tmp` exits 0 and is recorded as a success, having saved nothing | Write to `$EDULLM_CHECKPOINT_DIR`. [Training a model](olmo-core.md) has the rest |

## Where to go next

[Using the platform](the-platform.md) is the reference for the form, the corpora, the machines and the guards. Read it when you want a field explained, not before your first run.
