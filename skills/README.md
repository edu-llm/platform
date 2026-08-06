# A skill for your coding agent

Most people here work through Cursor, Claude Code or Codex rather than by typing commands.
An agent that has not been told about this platform writes a shell script that calls AWS,
which fails for anybody holding no AWS role and, for anybody holding one, works and leaves
no run that can be cited.

`edullm-platform/SKILL.md` is the file that stops that. It is self-contained. Copy it into
your own repository and your agent picks it up.

## Install it

All three hosts read the same file out of a folder of their own. Run the line for the host
you use, from the root of the repository you work in.

```bash
# Cursor
mkdir -p .cursor/skills/edullm-platform && curl -fsSL \
  https://raw.githubusercontent.com/edu-llm/platform/main/skills/edullm-platform/SKILL.md \
  -o .cursor/skills/edullm-platform/SKILL.md

# Claude Code
mkdir -p .claude/skills/edullm-platform && curl -fsSL \
  https://raw.githubusercontent.com/edu-llm/platform/main/skills/edullm-platform/SKILL.md \
  -o .claude/skills/edullm-platform/SKILL.md

# Codex
mkdir -p ~/.codex/skills/edullm-platform && curl -fsSL \
  https://raw.githubusercontent.com/edu-llm/platform/main/skills/edullm-platform/SKILL.md \
  -o ~/.codex/skills/edullm-platform/SKILL.md
```

Commit the file where you put it in a repository, so that everybody working in that
codebase gets it and nobody has to be told it exists. Swap `.cursor` for `~/.cursor` where
you would rather have it in every repository you open.

Re-run the line to pick up a newer copy. Nothing warns you that the one you have is old, so
re-run it when a release note says `edullm` gained a verb or a flag.

## Check that it took

Ask the agent to price a run without submitting it. In a repository the platform carries it
should reach for `edullm check --json` on its own and read the refusals back to you.

> Price a one-card smoke run of this repository on the platform. Do not submit it.

An agent that instead writes Python against `boto3`, or opens the GitHub Actions page, has
not loaded the skill. Check the path and the folder name against the lines above.

## What it does not cover

The skill drives the platform. It does not teach the science, and it does not know what
your training script needs. Two guides carry that, and a person reads them rather than an
agent.

- [Using the platform](../guides/the-platform.md), which is the same ground by hand.
- [Training a model](../guides/olmo-core.md), for OLMo-core specifically.
