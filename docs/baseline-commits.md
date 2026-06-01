# Baseline commits for comparisons

Agents and humans use this when asking how something worked **originally**, **in upstream Gaggimate**, or **before our fork changes**.

## upstream-original (Gaggimate original code)

| | |
|---|---|
| **Commit** | `69ce0c003279` (`69ce0c00`) |
| **Message** | Merge branch `master` of github.com:jniebuhr/gaggimate |
| **When** | Tree at first clone from upstream (`git reflog` → `clone: from github.com:jniebuhr/gaggimate.git`) |
| **Clone** | `git clone git@github.com:jniebuhr/gaggimate.git` |

This is the fixed “original Gaggimate code” baseline. It does not move.

Optional local tag (same commit):

```bash
git tag -f upstream-original 69ce0c00
git show upstream-original --oneline -s
```

## Other names (same commit)

- **gaggimate-original**
- **upstream at clone**
- **pre-fork upstream** (for “how did upstream do X before we changed anything in this repo”)

## Fork baselines (move over time)

| Name | Ref | Use when |
|------|-----|----------|
| **fork-published** | `origin/master` | “What’s on GitHub” / last pushed |
| **fork-head** | `HEAD` | Latest local commit |
| **working-tree** | uncommitted | “What we’re editing now” |
| **upstream-remote** | `upstream/master` | Current upstream (after `git fetch upstream`) |

Fork remote: `git clone git@github.com:phamhanh/gaggimate.git` (`origin`).

## Compare examples

```bash
git show 69ce0c00:src/display/ui/default/DefaultUI.cpp
git diff 69ce0c00 -- src/controller/heater.cpp
git log 69ce0c00..HEAD --oneline -- path/to/file
```

Cursor always loads `.cursor/rules/gaggimate-baseline-commits.mdc` so every chat knows these baselines.
