---
name: gaggimate-commit
description: >-
  At commit time, decide whether docs/this-fork.md needs an update for new upstream
  divergence (not a changelog). Use when user commits, says ready to commit, or ends
  a task with fork-relevant firmware changes.
---

# Gaggimate commit and fork docs

## Purpose — read this first

[`docs/this-fork.md`](../../docs/this-fork.md) documents **what differs from upstream** [jniebuhr/gaggimate](https://github.com/jniebuhr/gaggimate) and **why**, for my Iberital Express.

It is **not**:

- a git log or rolling notebook of commits
- a place to record bugfixes, refactors, or deploy-script tweaks
- a mirror of commit messages or agent session notes

**Do not append a new bullet for every commit.** Most commits need only a good commit message.

Put operational detail in [`scripts/README.md`](../../scripts/README.md) or [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

Only edit this-fork when the change introduces or materially changes **fork identity** — behavior or architecture someone would notice when diffing against upstream.

## The gate question

Before editing this-fork, ask:

> **Would someone diffing this repo against upstream need a new paragraph or bullet to understand a behavioral or architectural difference — not just a bugfix iteration on something already described?**

If no → skip this-fork (commit message alone is enough).

## When to run

- User asks to commit
- Agent asks “Ready to commit? I'll suggest a message.”
- Ending a multi-step task with uncommitted changes

## How to edit (never a changelog)

- **Prefer revising** an existing paragraph or bullet so it stays accurate (e.g. widen “profiles and shot history” to include notes if that was always the intent).
- **Add** only when upstream has no equivalent and the topic is not covered yet (e.g. WiFi reconnect behavior, PID freeze).
- **Never append** “fixed X in deploy”, “unified OTA module”, “version bump reads origin tags” — those are maintenance, not fork identity.

**What to write:** Durable fork rationale — problem on my machine → approach → why it differs from upstream. First person, machine-specific. Sourced from the agent conversation, **not** from commit messages or per-commit diffs.

## Section map

| Topic | Section in this-fork |
|-------|----------------------|
| Thermal / Kff / PID | Temperature during shots |
| Pressure / vent | Pressure between shots |
| WiFi / connectivity (if upstream differs) | WiFi and connectivity |
| Why I OTA from my Mac / SPIFFS backup concept | Releases and OTA / Deploy without losing profiles |
| One-off firmware/UI for this machine | Smaller fixes on this machine |
| Deep tuning | Dedicated doc (e.g. `thermal-kff-tuning.md`) + one-line pointer |

## Always skip this-fork for

Deploy/release script refactors, semver/tag logic, agent skill changes, CI, bugfixes within already-documented features, comment/typo-only edits, version bumps, upstream merges.

## Commit integration

1. Run the gate question on pending changes.
2. If it passes, update this-fork in the **same commit** as the code (stage together).
3. Suggest a commit message focused on the **why**, not a file list.
4. When the user asks to commit, follow **Git workflow** below (including the mandatory unlock at the end).

Do not duplicate long technical detail already in sibling docs; do not rewrite unrelated sections.

## Git workflow (required when committing)

Agent commits often leave a stale `.git/index.lock`. **Always** bookend mutating git with `./scripts/git-ensure-unlocked.sh` — especially **after** add/commit, even on success.

1. **Read-only prep in parallel is OK** (`git status`, `git diff`, `git log`). Never parallelize index **writes** (`add`, `commit`, `reset`, `stash`, `merge`).
2. **One shell, one chain** for add + commit (do not split across tool calls):

   ```bash
   ./scripts/git-ensure-unlocked.sh
   git add <paths> && git commit -m "$(cat <<'EOF'
   message
   EOF
   )"
   ./scripts/git-ensure-unlocked.sh
   ```

3. **Mandatory final step:** run `./scripts/git-ensure-unlocked.sh` again in its **own** shell call after the commit chain — success, failure, or user cancelled mid-commit. This cleans up locks the agent workflow leaves behind. Do not skip because the commit “succeeded.”
4. If the script exits 1 (lock exists and git is still running), wait or stop the other git process; do not `rm` the lock manually while Source Control or another terminal may be committing.
5. If add/commit failed with *Unable to create '.git/index.lock'*, run `./scripts/git-ensure-unlocked.sh` before retrying.

**On your machine:** avoid staging/committing in the Source Control panel while the agent is committing the same repo.
