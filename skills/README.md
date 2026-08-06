# The agent layer, and where each host reads it from

Most people here work through Cursor, Claude Code or Codex rather than by typing commands.
An agent that has not been told about this platform writes a shell script that calls AWS,
which fails for anybody holding no AWS role and, for anybody holding one, works and leaves
no run that can be cited.

Three files stop that, and they are the three the owner settled on. Two skills, which an
agent invokes when it recognises a situation, and one always-on rule, which needs no
invocation and is what makes the skills reachable at all.

| Artifact | What it is for |
| --- | --- |
| `.agents/skills/submitting-a-run/` | a run to price, submit or follow |
| `.agents/skills/registering-a-repository/` | a codebase the platform does not carry yet |
| `AGENTS.md` | the always-on rule: the binary exists, here are its verbs, do not call AWS |

This directory is not where they live. They live at the paths above, in this repository and
in every registered research repository, because a file only a platform checkout carries
loads for people working on the platform and for nobody else.

## What each host actually reads

Checked against vendor documentation on 2026-08-06. The three hosts do not agree, and the
disagreement is the whole reason the layout looks the way it does.

| Host | Root instruction file | Project skills directory |
| --- | --- | --- |
| Cursor | `AGENTS.md` | `.agents/skills/`, `.cursor/skills/`, and `.claude/skills/` and `.codex/skills/` for compatibility |
| Claude Code | `CLAUDE.md`, **and not `AGENTS.md`** | `.claude/skills/` only |
| Codex | `AGENTS.md` | `.agents/skills/` only |

Two things in that table are worth stating in words, because both have been got wrong here
before and both fail silently.

**`AGENTS.md` is not read by all three.** Cursor and Codex read it. Claude Code reads
`CLAUDE.md` and has no setting that turns `AGENTS.md` on. A repository carrying only
`AGENTS.md` is invisible to Claude Code, and a repository carrying only `CLAUDE.md` is
invisible to the other two. Both files have to exist, and one of them points at the other
so there is a single text rather than two that drift.

**No single directory reaches all three.** `.agents/skills/` is Codex's only project path
and Cursor reads it natively, so it covers two of the three. Claude Code reads nothing but
`.claude/skills/`. So `.claude/skills/<name>` is a **symlink** to the copy under
`.agents/skills/`, which is a documented-supported arrangement in Claude Code and is the
only one where the two paths cannot come to hold different text.

An earlier version of this file told people to `curl` the skills into a folder under their
Codex home directory. Codex does not read one, so anybody who followed it got nothing, and
nothing said so.

## Getting it into a repository

Do not copy it by hand. `tools/distribute_agent_layer.py` writes the layout into a checkout,
and the `agent-layer-is-distributed` workflow fails when a registered repository's copy stops
matching the one here.

```bash
uv run python tools/distribute_agent_layer.py /path/to/a/research/checkout
```

It writes the two skills under `.agents/skills/`, the two symlinks under `.claude/skills/`,
and the always-on rule into `AGENTS.md` between two markers. Anything already in that
`AGENTS.md` outside the markers is left alone, because the repository-specific half of it is
the half the platform has no business writing.

## Checking that it took

Ask the agent to price a run without submitting it.

> Price a one-card smoke run of this repository on the platform. Do not submit it.

It should reach for `edullm check --json` on its own and read the refusals back. An agent
that instead writes Python against `boto3`, or opens the GitHub Actions page, has not loaded
anything. Check the paths against the table above for the host you are actually using.

## What this does not cover

The skills drive the platform. They do not teach the science and they do not know what your
training script needs. Two guides carry that, and a person reads them rather than an agent.

- [Using the platform](../guides/the-platform.md), which is the same ground by hand.
- [Training a model](../guides/olmo-core.md), for OLMo-core specifically.
