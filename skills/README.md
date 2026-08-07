# The agent layer, and the one line you run

Most people here work through Cursor, Claude Code or Codex rather than by typing commands.
An agent that has not been told about this platform writes a shell script that calls AWS,
which fails for anybody holding no AWS role and, for anybody holding one, works and leaves
no run that can be cited.

Two artifacts stop that, and the split between them is the whole design.

| Artifact | What it is | Where it lives | Who installs it |
| --- | --- | --- | --- |
| the always-on rule | `agents-md-block.md`: the binary, its verbs, the exit codes, `--json`, never call AWS | committed in every registered repository | nobody, it is already there |
| `edullm-platform/SKILL.md` | the skill: the same ground in the detail an agent needs when it is actually driving a submission | a home directory, once per machine | you, with the line below |

## In a repository the platform already carries, install nothing

The rule is committed there, in `AGENTS.md` and imported into `CLAUDE.md`. Open the
repository and the agent has it. Nothing to run, nothing to remember and nothing in your
home directory that rots, because `agent-layer-is-distributed` compares every registered
repository's copy with the source here and goes red when they stop agreeing.

`edullm check` names the repositories the platform carries when it refuses one it does not.

## The one line, once per machine

The rule cannot reach the case it is most needed in: a codebase the platform does not carry
has no `AGENTS.md` for anybody to have written a rule into. The skill covers that, and it
carries the detail a rule has no room for. So it goes on the machine.

```bash
for skills in ~/.agents/skills ~/.claude/skills; do
  mkdir -p "$skills/edullm-platform" && curl -fsSL \
    https://raw.githubusercontent.com/edu-llm/platform/main/skills/edullm-platform/SKILL.md \
    -o "$skills/edullm-platform/SKILL.md"
done
```

Both directories, because no single one reaches all three hosts. Re-running it is the
upgrade, and it is worth re-running when a release note says `edullm` gained a verb or a
flag: nothing warns you that the copy you have is old, which is the price of a file in a
home directory and the reason only one file is there.

**A home directory and not a repository, which is a change from what this page used to
say.** It said to commit the file into the repository you work in. Do not, for two reasons
that both point the same way. Claude Code resolves a personal skill **over** a project one,
the reverse of most configuration systems, so a committed copy loses to whatever is in
somebody's home directory and a team cannot fix a colleague's stale copy by merging
anything. And every registered repository now ships the rule, so a committed skill beside it
is a second answer to questions the repository already answers.

### If you ran the older line

Nothing you have is broken and nothing you must do today. The copy on your disk still works
and the URL it came from still serves the same file. Two corrections, when you next think of
it:

```bash
rm -rf ~/.codex/skills/edullm-platform
```

That path is one Codex reads from its own source's deprecated fallback, so it worked by luck.
And where you committed the skill into a repository, delete that folder there and commit the
removal, for the precedence reason above.

Then run the line above. It is idempotent and overwrites whatever it finds.

## What each host actually reads

Checked against vendor documentation on 2026-08-06. The hosts do not agree, and the
disagreement is why the install line writes two directories. Every one of these fails
silently when it is wrong: an agent with no rule behaves exactly like an agent that read one
and ignored it.

| Host | Root instruction file | Project skills | User-level skills |
| --- | --- | --- | --- |
| Cursor | `AGENTS.md` | `.agents/skills/`, `.cursor/skills/`, and others for compatibility | `~/.agents/skills/`, `~/.cursor/skills/`, and `~/.claude/skills/` and `~/.codex/skills/` for compatibility |
| Claude Code | `CLAUDE.md`, **and not `AGENTS.md`** | `.claude/skills/` only | `~/.claude/skills/` only |
| Codex | `AGENTS.md` | `.agents/skills/` only | `~/.agents/skills/`, and `~/.codex/skills/` as a deprecated fallback |

**`AGENTS.md` is not read by all of them.** Cursor and Codex read it. Claude Code reads
`CLAUDE.md` and has no setting that turns `AGENTS.md` on. So every registered repository
carries both, and `CLAUDE.md` holds `@AGENTS.md` — Claude Code's documented import — rather
than a second copy of the text.

**Codex's user-level path is `~/.agents/skills/`, not `~/.codex/skills/`.** The older line on
this page wrote the latter. Codex does still read it, from a path OpenAI's own source comments
mark deprecated and kept for backward compatibility, so that instruction worked by luck.
`~/.agents/skills/` is the one that keeps working. No project-level `.codex/skills/` is read
by Codex at all; Cursor reads one, which is how that path came to look correct.

## Getting the rule into a repository

Do not copy it by hand and do not paste anything between the markers. The distributor writes
it, and everything outside the markers is left exactly as it was found, because the
repository-specific half of an `AGENTS.md` is the half the platform has no business writing.

```bash
uv run python tools/distribute_agent_layer.py /path/to/a/research/checkout
```

Then open a pull request against that repository. Nothing here pushes to one: every
registered repository is somebody's working codebase with people on branches.

## Editing either of them

**The two are edited independently and neither edit can break the other, which is on
purpose.** The install line names a path on `main`, so improving the skill's text needs no
coordination with anybody: merge it and the next person to run the line gets it.

What is not free is *moving* either file. Roughly the whole roster has the URL above in a
shell history, so a rename turns it into an error for everybody who runs it afterwards.
`tests/test_documented_urls_resolve.py` goes red in the pull request that does it, and names
the sentences that have to move with the file. Do not route around it by deleting the
sentence as well — that satisfies the test and breaks the roster, which is the one failure
this layer has already had.

## Checking that it took

Ask the agent to price a run without submitting it.

> Price a one-card smoke run of this repository on the platform. Do not submit it.

It should reach for `edullm check --json` on its own and read the refusals back. An agent that
writes Python against `boto3`, or opens the GitHub Actions page, has not loaded anything.
Check the paths against the table above for the host you are actually using.

## What this does not cover

The layer drives the platform. It does not teach the science and does not know what your
training script needs. Two guides carry that, and a person reads them rather than an agent.

- [Using the platform](../guides/the-platform.md), which is the same ground by hand.
- [Training a model](../guides/olmo-core.md), for OLMo-core specifically.
