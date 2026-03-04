You are resolving a git rebase conflict in a worktree. This is a single focused task.

## Rules

- Use tools directly and silently. Do NOT produce conversational text.
- Do NOT close, abandon, or delete any PR or branch.
- Do NOT create new PRs or issues.
- STOP immediately after the successful push.

## Task

A `git rebase` has left the worktree in a conflicted state. Resolve the conflicts, complete the rebase, and push the result.

## Steps

1. `git status` to identify conflicted files
2. For each conflicted file:
   - Read the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
   - For documentation/README files: accept the upstream (mainline) version
   - For source code: merge both sets of changes correctly, preserving all new functionality
3. `git add` each resolved file
4. `GIT_EDITOR=true git rebase --continue`
5. `git push --force-with-lease origin <branch>`
6. STOP. Your job is done.
