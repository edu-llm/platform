# The agent layer, and where each host reads it from

Most people here work through Cursor, Claude Code or Codex rather than by typing commands.
An agent that has not been told about this platform writes a shell script that calls AWS,
which fails for anybody holding no AWS role and, for anybody holding one, works and leaves
no run that can be cited.

Two artifacts stop that, and the split between them is the whole design.

| Artifact | What it is | Where it goes |
| --- | --- | --- |
| `AGENTS.md` | the always-on rule: the binary, its verbs, the exit codes, `--json`, never call AWS | committed in every registered repository |
| `registering-a-repository` | the one skill: multi-step, has a tool behind it, and has already cost an incident | user level, once per person |

`skills/agents-md-block.md` is the source of the first. It is roughly ninety per cent of what
an agent needs and it is what makes the skill reachable at all.

**There is no skill for submitting a run.** There was, and it was deleted. Its table of
refusal codes restated in prose what `edullm check --json` prints in the `detail` beside every
one of them, which is this layer's founding rule inverted: a skill reads the generated
artifact rather than describing it. Four lines in it were not in the rule and were folded into
the rule before it went — that the platform takes a commit and not a working tree, that
`--dataset none` and no `--dataset` are different answers, that `2>&1` corrupts the first
check in a fresh repository, and that `status --json` is free where `status` and `logs` are
not. A fifth, writing the dtype into the command so the bfloat16 guard can see it, went in
with its worked example, because it is the most expensive thing the layer teaches.

## What each host actually reads

Checked against vendor documentation on 2026-08-06. The three hosts do not agree, and the
disagreement is why the layout looks the way it does. Every one of these fails silently when
it is wrong: an agent with no rule behaves exactly like an agent that read one and ignored it.

| Host | Root instruction file | Project skills | User-level skills |
| --- | --- | --- | --- |
| Cursor | `AGENTS.md` | `.agents/skills/`, `.cursor/skills/`, and others for compatibility | `~/.agents/skills/`, `~/.cursor/skills/`, and `~/.claude/skills/` and `~/.codex/skills/` for compatibility |
| Claude Code | `CLAUDE.md`, **and not `AGENTS.md`** | `.claude/skills/` only | `~/.claude/skills/` only |
| Codex | `AGENTS.md` | `.agents/skills/` only | `~/.agents/skills/`, and `~/.codex/skills/` as a deprecated fallback |

Four things worth stating in words.

**`AGENTS.md` is not read by all three.** Cursor and Codex read it. Claude Code reads
`CLAUDE.md` and has no setting that turns `AGENTS.md` on. So every registered repository
carries both, and `CLAUDE.md` holds `@AGENTS.md` — Claude Code's documented import — rather
than a second copy of the text.

**No single directory reaches all three, at either level.** `~/.agents/skills/` covers Cursor
and Codex. Claude Code needs `~/.claude/skills/`. Two directories, or one and a symlink.

**Codex's user-level path is `~/.agents/skills/`, not `~/.codex/skills/`.** An earlier version
of this page told people to `curl` the skill into the latter. Codex does still read it, from a
path OpenAI's own source comments mark deprecated and kept for backward compatibility, so that
instruction worked and was working by luck. `~/.agents/skills/` is the one that will keep
working. Note also that no project-level `.codex/skills/` is read by Codex at all; Cursor
reads one, which is how that path came to look correct.

**Claude Code resolves a personal skill over a project one.** This is the reverse of most
configuration systems and it matters here: a stale copy in somebody's home directory silently
beats the copy a team committed. It is an argument against ever having both, and it is the
reason the one skill is user-level *only* and the rule is repository-level *only*.

## Why the skill is not committed to the six repositories

It fires when a codebase is **not** on the platform. The six registered repositories are the
six places that condition cannot arise, so committing it there installs it where it can never
be needed and nowhere it can.

That is the bootstrap problem and it is real: an unregistered codebase has no `AGENTS.md`
either, so nothing in the repository tells an agent the skill exists. Whatever reaches a
person has to reach them before the repository does.

## Getting the rule into a repository

Do not copy it by hand. `tools/distribute_agent_layer.py` writes it, and the
`agent-layer-is-distributed` workflow fails when a registered repository's copy stops matching
the one here.

```bash
uv run python tools/distribute_agent_layer.py /path/to/a/research/checkout
```

It splices the rule into `AGENTS.md` between two markers and adds the `CLAUDE.md` import.
Anything outside the markers is left alone, because the repository-specific half of that file
is the half the platform has no business writing.

## Getting the skill onto a person

Today, by hand, once.

```bash
mkdir -p ~/.agents/skills && cp -R .agents/skills/registering-a-repository ~/.agents/skills/
mkdir -p ~/.claude/skills && ln -s ~/.agents/skills/registering-a-repository ~/.claude/skills/
```

**This is the weak point of the layer and it is not solved.** "Run these two commands you have
never heard of" is how an install fails, and the people who most need the skill — somebody
bringing a new codebase to the platform — are the least likely to have read this page.

### Should `edullm` write it?

The case for is strong. Everybody has the binary, it already knows all three hosts, and a
first-run action is the only mechanism in the system that reaches a person before they need
anything. Nothing else can.

The case against is stronger than it first looks, and it is not squeamishness:

- A tool writing into `$HOME` unasked is a surprise, and it writes into directories shared
  with every other skill the person has installed from anywhere.
- The file lands outside version control, where nobody can see it, nothing compares it, and it
  goes stale silently. That is the exact defect committing the rule was meant to remove, moved
  from the repository to the home directory.
- **Claude Code's precedence makes a stale copy actively harmful**, not merely useless. A
  personal skill overrides a project one, so a copy written in March wins over anything
  committed later, and no change to any repository can dislodge it.

So: **not unasked, and not silently.** The shape that survives all three objections is an
explicit, idempotent verb that says what it wrote and where, plus a refusal that names it — so
the moment somebody actually hits `unregistered_repository`, the `detail` tells them the one
command to run. The refusal half of that is built and needs no home directory at all. The verb
is not, and is deliberately left as a decision rather than a fait accompli, because it needs
a version and an owner's yes.

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
