## Git discipline

After making any code change:
1. State which files were modified.
2. Apply **`.cursor/skills/gaggimate-commit/SKILL.md`** — if the change is new upstream divergence, update `docs/this-fork.md` (not a changelog; see skill Purpose block).
3. Ask the user: "Ready to commit? I'll suggest a message." before ending the task.
4. Never leave a session with uncommitted changes without explicitly flagging them.
5. Run `./scripts/check-uncommitted.sh` at the end of any multi-step task.
