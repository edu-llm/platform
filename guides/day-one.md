# Day one

From nothing to a run that printed a number. Read this one and nothing else. Every command below was run on 2026-08-06 and what it printed is what is written here.

## Install the tool

```
uv tool install --force git+https://github.com/edu-llm/platform
```

Four seconds. Then `edullm --version`, which prints the version and the commit it was built from.

**Re-run that same line to upgrade.** `--force` makes it idempotent, so the one line installs, upgrades and repairs. Do not reach for `uv tool upgrade`. For a tool installed from git it answers `Nothing to upgrade` however far behind you are, and exits 0, so it tells you that you are current when you are months behind. It then suggests installing `edullm`, and that fails too, because `edullm` is the command and `edullm-platform` is the package.

If the shell cannot find `edullm` afterwards, compare `uv tool dir --bin` against `which -a edullm`, or `where.exe edullm` on Windows. They should be the same directory. Two lines means something else called `edullm` is on your path first.

## You do not need AWS credentials

Sixteen of the thirty-five of us hold no AWS role, and none of us needs one to submit a run. `check`, `submit`, `status`, `logs` and `cancel` drive `git` and `gh` and hold no cloud credential. All five were run with a deliberately broken AWS environment on 2026-08-06 and all five answered normally.

What you do need is `gh auth login`. That is the whole of it. If `check` refuses with `submitter_unknown`, it could not read who you are and that is the command that fixes it.

Two verbs are the exception and they are not the submission path. `edullm run` and `edullm shell` start a machine of your own, and those assume an AWS identity. If you want one, file `edullm ask --kind access-request`. Nothing else here waits on that.

## Your first job

A real one. It times an attention block on one card across four sequence lengths and prints forward milliseconds, backward milliseconds and tokens per second for each.

```
git clone https://github.com/edu-llm/OLMo-core
cd OLMo-core
git checkout edullm/onboarding-smoke
edullm check --team scratch --experiment day-one --dataset none
```

`check` reaches no network, answers in about a fifth of a second, and lists every refusal at once. Read the ceiling and the approval line out of what it prints rather than out of any document, because both live in reviewed configuration and move. On this branch it prints no refusals, a ceiling of well under a dollar on one T4, and an approval line saying nobody has to release it.

```
edullm submit --team scratch --experiment day-one --dataset none
```

The run id is minted a couple of minutes later by the compile job, and `submit` is meant to wait for it. On three real submissions tonight it returned in under nine seconds instead, with a workflow link and a line saying the id is still compiling. Either way `edullm status` carries the id once that job has finished.

## Reading it back

```
edullm status                       # your recent runs, and the run id
edullm status run_019fd5d7-915f     # one run, in full
edullm logs run_019fd5d7-915f       # the last lines it printed
```

`status` with no argument answers from GitHub in about ten seconds. `status` on one run and `logs` reach AWS, which they do by dispatching a workflow and waiting for a runner, so give them one to three minutes. A job sitting at `RUNNABLE` is waiting for a machine and is billing nothing. Do not cancel and resubmit it, because that puts it at the back of the queue.

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

## The notification, and why you will not get one

When a run ends the platform composes one line for the `runs` channel. It looks like this.

```
[runs] Aryan Verma · plan-b-phase0-100m-superbpe-eval · $0.02 spent, $2.01 authorised · ran 1m on gpu-1xa10g.
[runs] Aryan Verma · plan-b-phase0-100m-superbpe-eval · $0.70 spent, nothing produced · died at 42m on gpu-1xa10g, exit 1, whether a checkpoint survived is unknown.
```

**Nothing sends them yet.** The slice is deployed and the webhook it posts to has never been supplied, so no message has been read end to end. Until one is, `edullm status` is how you find out your run ended. Poll it, or look at the run page the submit line printed.

## Running your own code

Your commit has to have been built into an image before a run can name it, and a push to a branch under `edullm/` is what builds one. So the loop on a repository that has never been submitted from is five steps rather than two.

1. `edullm check`. On a registered repository with no spec it writes a first `.edullm/run.yaml` and then refuses, because the file it just wrote is not committed. That is expected and it says so.
2. Commit that file. `check` now refuses with `commit_not_pushed` instead.
3. Push the branch under `edullm/`. The image build takes three to eight minutes.
4. `edullm check` again. It should print no refusals.
5. `edullm submit`.

Two checks are deferred to submit time and `check` names both, because they need the container registry and this tool holds no credential for it. A clean `check` is not a promise.

## Walls still standing

| Wall | The way round |
| --- | --- |
| No Windows machine has ever finished this. The install used to fail with `Filename too long` for any username over eight characters, and [#291](https://github.com/edu-llm/platform/pull/291) fixed that on 2026-08-06 along with the `gh` lookup, the spec's line endings and redirected output. All of it is untested on real Windows | Follow it anyway and say where it stopped. If the install still fails on a path, point `UV_CACHE_DIR` at something short such as `C:\uv` and try again |
| No notification is delivered | Poll `edullm status`, as above |
| `edullm status <run-id>` prints `Container said` `nothing` for a run that printed plenty. Measured on the run above, which had nine lines waiting | `edullm logs <run-id>` reads the same stream and does show them. Believe that one |
| `edullm submit` returns before the run id exists, though `--help` says it waits for one and `--no-wait` is the flag that turns that off | Run `edullm status` a couple of minutes later. The id is there |
| The eval image carries no torch and no vLLM, so only the `mock` provider runs | Nothing yet. GPU evaluation through the platform is not available. eval-inference owns the choice |
| `gh` in a clone that has an `upstream` remote answers about the wrong repository, with no warning. In OLMo-core it reported no image build for a branch whose build had succeeded | Use `edullm`, which reads `origin`. Where you must use `gh`, pass `--repo edu-llm/<name>` |
| Roughly half of all runs fail, and about half of those failures print no cause | Nothing yet. Your first failure is probably not your fault. Bring the run id to an issue |
| A run that writes its checkpoints to `/tmp` exits 0 and is recorded as a success, having saved nothing | Write to `$EDULLM_CHECKPOINT_DIR`. [Training a model](olmo-core.md) has the rest |

## Where to go next

[Using the platform](the-platform.md) is the reference for the form, the corpora, the machines and the guards. Read it when you want a field explained, not before your first run.
