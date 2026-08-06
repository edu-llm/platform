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

What you do need is `gh auth login`. That is the whole of it.

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

That returns in seconds with a workflow link. It does not wait and it does not print the run id, because the run id is minted a couple of minutes later by the compile job. `edullm status` carries it once that job has finished.

## Reading it back

```
edullm status                       # your recent runs, and the run id
edullm status run_019fd5d7-915f     # one run, in full
edullm logs run_019fd5d7-915f       # the last lines it printed
```

`status` with no argument answers from GitHub in about ten seconds. `status` on one run and `logs` reach AWS, which they do by dispatching a workflow and waiting for a runner, so give them up to three minutes. A job sitting at `RUNNABLE` is waiting for a machine and is billing nothing. Do not cancel and resubmit it, because that puts it at the back of the queue.

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
| A Windows install can fail with `Filename too long` if your username is longer than eight characters, because a tracked path here reaches 174 characters and Git for Windows stops at 260 | [#291](https://github.com/edu-llm/platform/pull/291) fixes it and is open with green checks. Until it merges, set `UV_CACHE_DIR` to something short such as `C:\uv` before installing |
| No notification is delivered | Poll `edullm status`, as above |
| The eval image carries no torch and no vLLM, so only the `mock` provider runs | Nothing yet. GPU evaluation through the platform is not available. eval-inference owns the choice |
| `gh` in a clone that has an `upstream` remote answers about the wrong repository, with no warning. In OLMo-core it reported no image build for a branch whose build had succeeded | Use `edullm`, which reads `origin`. Where you must use `gh`, pass `--repo edu-llm/<name>` |
| Roughly half of all runs fail, and about half of those failures print no cause | Nothing yet. Your first failure is probably not your fault. Bring the run id to an issue |
| A run that writes its checkpoints to `/tmp` exits 0 and is recorded as a success, having saved nothing | Write to `$EDULLM_CHECKPOINT_DIR`. [Training a model](olmo-core.md) has the rest |

## Where to go next

[Using the platform](the-platform.md) is the reference for the form, the corpora, the machines and the guards. Read it when you want a field explained, not before your first run.
