## Baselines for “originally” / “before our changes”

- **upstream-original** (Gaggimate original code): `69ce0c00` — tree at clone from `jniebuhr/gaggimate`. See `.cursor/rules/gaggimate-baseline-commits.mdc` and `docs/baseline-commits.md`.

## Git discipline

After making any code change:
1. State which files were modified.
2. Apply **`.cursor/skills/gaggimate-commit/SKILL.md`** — if the change is new upstream divergence, update `docs/this-fork.md` (not a changelog; see skill Purpose block).
3. Ask the user: "Ready to commit? I'll suggest a message." before ending the task.
4. Never leave a session with uncommitted changes without explicitly flagging them.
5. Run `./scripts/check-uncommitted.sh` at the end of any multi-step task.
6. When committing, follow the commit skill **Git workflow**: `./scripts/git-ensure-unlocked.sh` before add/commit, chain in one shell, then **`./scripts/git-ensure-unlocked.sh` again afterwards** (mandatory — agents often leave a stale lock).
