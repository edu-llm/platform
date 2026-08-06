# Working with the eduLLM platform

Read this before you write a script that talks to AWS. There is a binary, it is the only
supported way in, and it holds no cloud credential of its own.

## What `edullm` is

`edullm` submits and follows runs on the eduLLM platform so that nobody has to open the
GitHub Actions UI. It drives `git` and `gh` and nothing else. Every AWS credential lives in a
workflow whose trust policy pins it to one file on `main`, so a laptop cannot obtain one and
should not try. A script that reaches past this binary either fails, for the people who hold
no AWS role, or succeeds and leaves no record, for the people who do. Both outcomes are worse
than the refusal you were trying to get around.

Install and version.

```bash
uv tool uninstall edullm-platform
uv tool install --force git+https://github.com/edu-llm/platform
edullm --version
```

Unpinned on purpose. That line is true after every release and re-running it is the upgrade,
where a tag written here would be a version this file has to be edited for.

The first line is a one-time repair for an install made before v4.2.2, and only for that. The
distribution used to be called `edullm-platform` while the command was `edullm`, so `uv tool
list` printed a word nobody types and `uv tool uninstall edullm` answered `not installed` to
somebody holding the binary. It is `edullm` now, and the two names agree. **Run it before the
install and not after.** Both entries own the same `edullm` executable, and uv deletes that
file when it removes the old entry without noticing the new install still needs it, so the
wrong order leaves `uv tool list` reporting a healthy `edullm` and nothing on the path. If you
have already done it that way round, re-run the install line. On a machine that never had the
old name it prints ``error: `edullm-platform` is not installed`` and exits 2, which is the
expected answer and costs nothing.

`uv tool upgrade` follows the ref the install named, so what it does depends on how the tool got
here. From the bare URL above it re-resolves the default branch and upgrades. From a release
note's line, which pins that release's tag, it answers `Nothing to upgrade` and exits 0 however
far behind the install is. Re-install with `--force`, which is the upgrade for both.

## The verbs

| Verb | What it does |
| --- | --- |
| `edullm check` | Prices a submission from this working tree and lists every refusal. Reaches no network. Writes a first `.edullm/run.yaml` where a registered repository has none. |
| `edullm submit` | Runs those checks and then dispatches the submission workflow. |
| `edullm status` | Names your recent submissions, or describes one run. |
| `edullm logs` | The last lines one run printed. |
| `edullm cancel` | Stops one admitted run, with a reason that goes on the record. |
| `edullm add` | Teaches the platform about a repository, dataset, shape, model or person. Produces a configuration pull request. |
| `edullm ask` | Files one ask for something you need yourself. Produces an issue somebody answers. |
| `edullm run` | Ships this working tree to a machine of your own and streams the output of the command after a bare `--` back. Ungated, and no run anybody can cite. |
| `edullm shell` | A terminal on that same machine, or a notebook on it with `--notebook`. |
| `edullm stop` | Ends the machine those two started, and says what it ran up and where your files are. |

Every verb in `BUILT_TODAY` is here and all of them are built. A bare `edullm` prints the list,
and `edullm <verb> --help` prints what that verb takes.

The last three are the exploration route and they are not the submission path. Nothing they do
is checked against the registry, priced, approved or written to a lineage record, so what
comes off them is a thing you saw rather than a result anybody can cite. Reach for `check` and
`submit` for anything that is meant to count.

**`edullm stop` terminates rather than stopping, and that is worth knowing before you type it.**
The machine's own disk goes with it. The scratch prefix survives, `edullm run` syncs that prefix
down before your command and back up after it, and a new machine for the same project picks up
where the old one left off. Stopping instead would leave a machine no verb here can find and
nothing reclaims, billing its volume for ever. It reaches only a machine tagged with your own
source identity, and there is deliberately no flag that names an instance id.

## Start with `check`, always

`edullm check` reaches no network, queues nothing and costs a fraction of a second. It lists
every refusal at once rather than one per attempt, so the loop is edit, check, edit, check,
and only then submit. Submitting to find out what is wrong spends a queue wait and somebody's
approval to learn what `check` would have said for free.

Two checks are deferred to submit time and `check` names both. They need the container
registry, which needs a credential this binary does not hold. A clean `check` is not a
promise that a submission will go through.

## Read the machine-readable form

`edullm check --json` and `edullm status --json` print one JSON document on stdout whatever
the outcome. Use them. Do not parse the paragraphs, which are written for a person and get
reworded.

```bash
edullm check --json --experiment context-length-sweep --dataset regmix-10b-v1
edullm status --json
edullm status --json run_019fcf3c-9878
```

Every document carries `format_version`, `edullm_version` and `verb`. A refusal is a list
under `refusals`, and every entry is a `code` and a `detail`. **Match on the code.** The
detail is prose and will change.

`edullm status --json` answers from GitHub and dispatches nothing, so it costs no runner and
you may call it in a loop. Where the answer has moved into AWS the document says
`needs_a_dispatch`, and the same verb without `--json` is what pays for that answer.

The other three verbs have no `--json` and are not getting one. What they print is a section
of a workflow job log, and there is no structure under it.

## Exit codes

Branch on these before you read anything.

| Code | Meaning |
| --- | --- |
| 0 | it stands |
| 1 | refused on the merits, and the refusal codes say why |
| 2 | the tool could not be driven, by input or by installation |
| 3 | the platform could not be asked |
| 130 | interrupted |

3 is the only one worth retrying. 1 means something has to change and retrying it unchanged
reaches the same place. 2 means the command itself was wrong.

## Push a branch when its first commit exists

Not when the work is finished. `git push -u origin HEAD` on a branch nobody has reviewed
changes nothing on `main`, costs nothing, and is the only artifact another session can find.
A branch that exists only in a worktree is invisible to everybody, including whoever picks the
work up tomorrow, which is usually you with none of the context.

The evening of 2026-08-05 produced two cases and both had already cost the time they were
going to cost by the time anybody noticed. The repository registration tool was reworked,
finished, left unpushed, and then reworked from scratch hours later by a different session;
driving both implementations over the same templates produced byte-identical output, and the
second author had no way to know the first existed. A guard against module rebinding, 524
lines, sat on one laptop and no remote until it was rebased over eighty-one commits, whereupon
it went red immediately on a loader written days after the branch was cut, by somebody who had
read neither the incident nor the file describing it, because that file was in a worktree
nobody could see.

Push first, keep pushing, and let the branch be the record. A draft pull request is better
still where the work is going to become one.

## Never do these

- Do not call AWS. No `boto3`, no `aws` CLI, no `curl` at an AWS endpoint. The binary is the
  interface and the workflows hold the credentials.
- Do not pass `--force` to `submit` to get past a refusal. Every refusal it skips is one
  admission makes again from inside AWS, so it buys a queue wait rather than an outcome.
- Do not quote a price, a runtime bound, a cost ceiling or a count of approvers from memory or
  from a document. Run `edullm check --json` and read it from the output. Those numbers live
  in reviewed configuration files and change without anybody telling you.
- Do not edit `.edullm/run.yaml` to make a refusal go away without reading what the refusal
  says. It usually names the field and the file to change.
- Do not commit a secret, a credential or a token into a research repository. The image is
  built from the commit.

## Where to go next

- `edullm check --help` for the fields one submission takes.
- `.cursor/skills/submitting-a-run/` when somebody asks you to run something.
- `.cursor/skills/registering-a-repository/` when the platform does not carry this codebase.
- `guides/the-platform.md` for how a person does all of this by hand.
