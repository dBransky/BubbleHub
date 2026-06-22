---
name: commit-last-to-pr
description: Prepare local changes for a pull request without committing or pushing. Use when asked to prepare the last changes for a PR, update PR documentation, or make a branch ready for review; requires scanning every commit included in the PR and updating README.md, CONTRIBUTING.md, docs/, and other documentation when behavior or user-facing workflows change.
---

# Prepare Last Change For PR

## Workflow

Use this skill when the user wants the latest work prepared for a pull request. Do not create commits and do not push. The goal is to inspect the full PR range and update documentation if needed.

1. Establish the PR context:
   - Run `git status --short --branch`.
   - Run `git diff` and `git diff --staged`.
   - Check whether the current branch has an open PR with `gh pr view --json url,baseRefName,headRefName,state`.
   - If there is no open PR, determine the intended base branch from the user or repository default.
   - Try to infer what issue this PR closes, treating the branch name as the leading signal. Look for issue numbers or slugs in the branch name first, then use commit messages, existing PR metadata, nearby issue references, or repository issue tracker results to confirm. If an issue is confidently identified, include a closing reference such as `Closes #123` or `Closes https://github.com/<owner>/<repo>/issues/123` in the suggested PR body. Do not invent an issue; if none can be inferred, say so in the final summary.

2. Scan every commit that belongs to this PR branch, not only the last commit:
   - Find the base branch from the PR, for example `main`.
   - Fetch or inspect the remote base if needed.
   - Use `git merge-base HEAD origin/<base>` when available.
   - Review `git log --oneline --decorate <base-or-merge-base>..HEAD`.
   - Review `git diff --stat <base-or-merge-base>..HEAD`.
   - Review `git diff <base-or-merge-base>..HEAD` enough to understand the full PR behavior.
   - Include staged and unstaged changes in the review if they are part of the current preparation work.

3. Update docs when the PR changes user-facing behavior:
   - Always check `README.md`, `CONTRIBUTING.md`, `docs/`, CLI help text, examples, configuration docs, and release notes as relevant.
   - Update docs for new flags, changed defaults, new environment variables, security/sandboxing changes, setup changes, and behavior users rely on.
   - If docs are not needed, be ready to explain why in the final summary.

4. Prepare the working tree:
   - Include only relevant files.
   - Do not stage or commit secrets, local credentials, generated caches, or unrelated user changes.
   - Run focused tests or checks that match the change risk.
   - If checks modify files, inspect the modifications before continuing.

5. Stop before committing:
   - Do not run `git add`, `git commit`, `git push`, or `gh pr create` unless the user separately and explicitly asks for those actions.
   - If useful, draft a suggested commit message for the user, but leave the repository uncommitted.
   - If the user later asks to commit, follow the normal git safety protocol for commits.

6. Report the result:
   - Summarize the full PR range reviewed, not just the last commit.
   - Mention docs updates, tests run, and any skipped checks or residual risk.
   - Mention the inferred closing issue reference, or explicitly say that no closing issue could be inferred.
   - Mention that no commit or push was performed.
   - If there is an existing PR, include its URL.
   - End with a **Next steps** section containing copy-pasteable commands for the user to run in this order:
     1. Stage the prepared files.
     2. Amend the existing commit. This is important: use `git commit --amend`, not a new commit, unless the user explicitly says they want a separate commit.
     3. Push the branch.
     4. Create the PR if one does not already exist.

Use this template for the final commands and replace placeholders with the actual files, branch, title, and body:

```bash
git add <updated-docs-and-related-files>
git commit --amend
git push
gh pr create --title "<title>" --body "<body including Closes #123 or a full issue URL when one was inferred>"
```

If the branch has no upstream, use `git push -u origin HEAD`. If a PR already exists, omit `gh pr create` and include the existing PR URL instead.

## PR Review Checklist

Before finishing, verify:

- [ ] The diff being prepared is intentional across the full PR commit range.
- [ ] The last commit does not hide regressions introduced by earlier commits on the branch.
- [ ] Docs are updated or explicitly not needed.
- [ ] Tests/checks appropriate to the change have passed or failures are explained.
- [ ] Any likely closing issue was inferred and included as `Closes ...`, or the final summary explains that no issue could be inferred.
- [ ] No secrets or unrelated local files are modified by this workflow.
- [ ] The branch is not the default branch unless the user explicitly asked to prepare direct default-branch changes.
- [ ] No commit, push, or PR creation was performed.

## Useful Commands

```bash
git status --short --branch
gh pr view --json url,baseRefName,headRefName,state
git merge-base HEAD origin/<base>
git log --oneline --decorate <merge-base>..HEAD
git diff --stat <merge-base>..HEAD
git diff <merge-base>..HEAD
git diff
git diff --staged
```
