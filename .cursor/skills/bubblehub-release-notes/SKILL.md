---
name: bubblehub-release-notes
description: Write BubbleHub release notes from git commits using the repository release template. Use when preparing a BubbleHub release, creating release notes, filling .github/release_template.md, summarizing commits since the last tag, or publishing release announcements. Always finish with the bash commands to commit the notes and push the release tag.
---

# BubbleHub Release Notes

## Workflow

When preparing a release, write the release notes yourself from git history:

1. Determine the target tag from the user or branch context, for example `v0.1.0`.
2. Find the previous release tag:

```bash
git describe --tags --abbrev=0 <target-tag>^
```

If the target tag does not exist yet, use the latest existing tag as the previous release.

3. Read commits since the previous release:

```bash
git log --oneline <previous-tag>..HEAD
```

4. Read `.github/release_template.md`.
5. Replace `vX.Y.Z` and `X.Y.Z` placeholders with the target tag/version.
6. Fill the template with concise, user-facing notes grouped into:
   - New Features
   - Improvements
   - Bug Fixes
   - Security & Sandboxing
7. Save the finished notes to:

```text
.github/releases/<target-tag>.md
```

Example:

```text
.github/releases/v0.1.0.md
```

8. After saving the notes, always tell the user how to publish the release. End your response with a **Next step** section containing the exact bash commands to run.

The release workflow triggers on a pushed `v*` tag and uses `.github/releases/<tag>.md` from that commit. The notes file must be committed before tagging.

Use this pattern (replace `<tag>` with the target tag, e.g. `v0.1.0`):

```bash
git add .github/releases/<tag>.md
git commit -m "Add release notes for <tag>"
git push origin HEAD
git tag <tag>
git push origin <tag>
```

Before giving these commands:

- Confirm CI is green on the commit you are about to tag.
- If the user is not on the default branch, use that branch name instead of `HEAD` in `git push`.
- If `<tag>` already exists locally or on the remote, say so and do not repeat create/push tag commands blindly.

Keep the command block copy-pasteable. Do not omit this section when the user asked for release notes.

## Rules

- Write for users, not only maintainers.
- Mention the Docker image when release work includes container publishing.
- Keep bullets concrete and based on commits.
- Do not invent changes not supported by commit history.
- Prefer short `BubbleHub.ai` download links in release notes:
  - Latest asset base: `https://BubbleHub.ai/download/latest`
  - Version asset base: `https://BubbleHub.ai/download/<tag>`
  - Linux installer: `https://BubbleHub.ai/install.sh`
  - Windows installer: `https://BubbleHub.ai/install.ps1`
- Use versioned filenames in package examples, for example `BubbleHub-0.1.0-x64.deb` and `BubbleHub-0.1.0-x64.exe`.
- Preserve the installation section structure from `.github/release_template.md`.
- Keep GitHub links for repository context, not for primary install commands.
- In the Contributors section, use the generic template line. Do not name Daniel Bransky or dBransky; he is the project inventor, not an external contributor to thank.

The release workflow uses `.github/releases/<tag>.md` when present. If the file is absent, GitHub generated release notes are used as a fallback.
