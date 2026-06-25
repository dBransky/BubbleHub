---
name: commit-last-to-pr
description: Prepare local changes for a pull request without committing or pushing. Use when asked to prepare the last changes for a PR, update PR documentation, or make a branch ready for review; requires scanning every commit included in the PR and updating README.md, CONTRIBUTING.md, docs/, and other documentation when behavior or user-facing workflows change.
---

# Prepare Last Change For PR

## Workflow

Use this skill when the user wants the latest work prepared for a pull request. Do not create commits and do not push. The goal is to inspect the full PR range, update documentation if needed, and draft a PR description that follows the repository template.

1. Establish the PR context:
   - Run `git status --short --branch`.
   - Run `git diff` and `git diff --staged`.
   - Check whether the current branch has an open PR with `gh pr view --json url,baseRefName,headRefName,state,body`.
   - If there is no open PR, determine the intended base branch from the user or repository default.
   - Read `.github/pull_request_template.md` before drafting or updating the PR body. Every PR description must follow that template structure.

2. Infer the related issue:
   - Treat the branch name as the leading signal. Look for issue numbers or slugs in the branch name first, then use commit messages, existing PR metadata, nearby issue references, or repository issue tracker results to confirm.
   - Fill the **Related Issue** section with `Closes #123` or `Closes https://github.com/<owner>/<repo>/issues/123` when confident.
   - Do not invent an issue. If none can be inferred, leave a short note in **Additional Notes** explaining that no issue was linked.

3. Scan every commit that belongs to this PR branch, not only the last commit:
   - Find the base branch from the PR, for example `main`.
   - Fetch or inspect the remote base if needed.
   - Use `git merge-base HEAD origin/<base>` when available.
   - Review `git log --oneline --decorate <base-or-merge-base>..HEAD`.
   - Review `git diff --stat <base-or-merge-base>..HEAD`.
   - Review `git diff <base-or-merge-base>..HEAD` enough to understand the full PR behavior.
   - Include staged and unstaged changes in the review if they are part of the current preparation work.

4. Update docs when the PR changes user-facing behavior:
   - Always check `README.md`, `CONTRIBUTING.md`, `docs/`, CLI help text, examples, configuration docs, and release notes as relevant.
   - Update docs for new flags, changed defaults, new environment variables, security/sandboxing changes, setup changes, and behavior users rely on.
   - If docs are not needed, be ready to explain why in the final summary and leave the **Documentation** checkboxes unchecked with a note in **Additional Notes**.

5. Prepare the working tree:
   - Include only relevant files.
   - Do not stage or commit secrets, local credentials, generated caches, or unrelated user changes.
   - Run focused tests or checks that match the change risk.
   - If checks modify files, inspect the modifications before continuing.

6. Draft the PR body from the template:
   - Start from `.github/pull_request_template.md`.
   - Remove HTML comments and placeholder text.
   - Fill every section with concrete content from the full PR diff and test results.
   - Mark applicable **Type of Change** checkboxes with `[x]`; leave others as `[ ]`.
   - Mark **Testing** and **Documentation** checkboxes to reflect what was actually done.
   - Use **Test Details** for commands run, test files added, and manual verification steps.
   - Use **Additional Notes** for reviewer context, follow-ups, migration notes, or residual risk.
   - Omit **Screenshots** entirely when not applicable; do not leave an empty optional section with only placeholder text.

7. Stop before committing:
   - Do not run `git add`, `git commit`, `git push`, or `gh pr create` unless the user separately and explicitly asks for those actions.
   - If useful, draft a suggested commit message for the user, but leave the repository uncommitted.
   - If the user later asks to commit, follow the normal git safety protocol for commits.

8. Report the result:
   - Summarize the full PR range reviewed, not just the last commit.
   - Mention docs updates, tests run, and any skipped checks or residual risk.
   - Include the filled PR body (or a clear draft) in the final summary so the user can review it before opening the PR.
   - Mention that no commit or push was performed.
   - If there is an existing PR, include its URL and note whether the body should be updated to match the template.
   - End with a **Next steps** section containing copy-pasteable commands.

Use this command sequence and replace placeholders with the actual files, branch, title, and filled template body. Prefer writing the body to a temporary Markdown file and passing it with `--body-file`; this keeps the final command readable and avoids nested quoting in long PR templates:

```bash
cat > /tmp/ageos-pr-body.md <<'EOF'
# Pull Request

## 🔗 Related Issue

Closes #123

---

## 📝 Summary of Changes

- ...

---

## 🏷️ Type of Change

* [x] ✨ New feature
* [ ] 🐛 Bug fix
* [ ] ♻️ Refactor
* [ ] 📝 Documentation update
* [ ] 🎨 UI/UX improvement
* [ ] ⚡ Performance improvement

---

## ✅ Testing

* [x] Existing tests pass
* [x] New tests added (if applicable)
* [x] Manually tested changes

### Test Details

- ...

---

## 📚 Documentation

* [x] Documentation updated
* [ ] README updated (if needed)
* [ ] Comments added/updated where appropriate

---

## 🚀 Additional Notes

- ...

EOF

git add <updated-docs-and-related-files>
git commit --amend
git push
gh pr create --title "<title>" --body-file /tmp/ageos-pr-body.md
```

If the branch has no upstream, use `git push -u origin HEAD`. If a PR already exists, omit `gh pr create` and update it instead:

```bash
cat > /tmp/ageos-pr-body.md <<'EOF'
...filled template body...
EOF

gh pr edit --body-file /tmp/ageos-pr-body.md
```

Prefer the temporary-file HEREDOC form above so the body matches `.github/pull_request_template.md` exactly while keeping the `gh` command simple. Do not substitute a free-form Summary/Test plan block unless the user explicitly asks for a shorter body.

## PR Review Checklist

Before finishing, verify:

- [ ] The diff being prepared is intentional across the full PR commit range.
- [ ] The last commit does not hide regressions introduced by earlier commits on the branch.
- [ ] Docs are updated or explicitly not needed.
- [ ] Tests/checks appropriate to the change have passed or failures are explained.
- [ ] The PR body follows `.github/pull_request_template.md` with filled sections and accurate checkboxes.
- [ ] Any likely closing issue was inferred and included as `Closes ...`, or the final summary explains that no issue could be linked.
- [ ] No secrets or unrelated local files are modified by this workflow.
- [ ] The branch is not the default branch unless the user explicitly asked to prepare direct default-branch changes.
- [ ] No commit, push, or PR creation was performed.

## Useful Commands

```bash
git status --short --branch
gh pr view --json url,baseRefName,headRefName,state,body
git merge-base HEAD origin/<base>
git log --oneline --decorate <merge-base>..HEAD
git diff --stat <merge-base>..HEAD
git diff <merge-base>..HEAD
git diff
git diff --staged
cat .github/pull_request_template.md
```
