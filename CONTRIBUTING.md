# Contributing to AgeOS

Thank you for your interest in contributing to AgeOS!

## Getting Started

1. Fork the repository to your GitHub account.
2. Clone your fork locally:

```bash
git clone https://github.com/<your-username>/ageos-runtime.git
cd ageos-runtime
```

3. Add the upstream repository:

```bash
git remote add upstream https://github.com/ageos-labs/ageos-runtime.git
```

4. Install the development tooling used by CI and local hooks:

```bash
python -m pip install -e '.[dev]'
pre-commit install
```

The `pre-commit install` step enables the repository's lint hooks so they run automatically on each local commit. Before opening a pull request, you can also run them manually with:

```bash
pre-commit run --all-files
```

## Branch Naming

Create a new branch for each change:

```bash
git checkout -b docs/add-contributing-guide
```

Recommended prefixes:

* `feat/` - New features
* `fix/` - Bug fixes
* `docs/` - Documentation changes
* `test/` - Test improvements
* `refactor/` - Code refactoring

Examples:

```text
feat/local-model-selection
fix/sandbox-network-access
docs/update-install-guide
```

## Commit Messages

Use concise commit messages following this format:

```text
type: short description
```

Examples:

```text
docs: add contribution guide
fix: handle missing model configuration
feat: add runtime cache cleanup
```

Include additional details in the commit body when necessary.

## Testing Requirements

Before opening a pull request, run the same tests used in CI.

### Unit Tests

CI runs libageos C unit tests and Python unit tests together:

```bash
docker build -f docker/Dockerfile --target unit-test -t ageos-runtime:unit .
docker run --rm --privileged --security-opt seccomp=unconfined ageos-runtime:unit
```

To run only the libageos Meson tests locally:

```bash
meson setup libageos/build libageos --prefix=/usr/local
meson compile -C libageos/build
meson test -C libageos/build --print-errorlogs
```

C tests live under `libageos/tests/` and link against the built `libageos.so`. Mount-related overfs tests require privileges and skip automatically in unprivileged environments; CI runs them inside the privileged Docker unit-test image.

### Coverage

CI uploads C and Python Cobertura reports to [Codecov](https://codecov.io/gh/ageos-labs/ageos-runtime). The project target is 45% line coverage (see `codecov.yml`). To reproduce the coverage run locally:

```bash
docker build -f docker/Dockerfile --target unit-test \
  --build-arg MESON_COVERAGE=true -t ageos-runtime:unit-cov .
mkdir -p .ci-artifacts/coverage
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$PWD/.ci-artifacts/coverage:/coverage-out" \
  ageos-runtime:unit-cov scripts/ci/run-unit-tests-coverage.sh
```

HTML reports are written under `.ci-artifacts/coverage/`. CI also keeps those reports as workflow artifacts; Codecov provides the dashboard, PR comments, and README badge.

### Integration Tests

```bash
docker volume create ageos-cache-local
docker volume create ageos-openclaw-local

docker build -f docker/Dockerfile --target integration-test -t ageos-runtime:integration .

docker run --rm --privileged --security-opt seccomp=unconfined \
  -v ageos-cache-local:/cache/ageos \
  -v ageos-openclaw-local:/cache/openclaw \
  ageos-runtime:integration
```

For an interactive shell in the same image (sandbox exploration, OpenClaw, MCP experiments), see [Interactive Docker Development](README.md#interactive-docker-development) in the README.

Ensure all tests pass before submitting a pull request.

## Keeping Your Fork Updated

Before creating a pull request:

```bash
git fetch upstream
git rebase upstream/main
```

Resolve any conflicts and verify tests still pass.

## Pull Request Expectations

Before opening a PR:

* Ensure your branch is up to date with `main`
* Run unit and integration tests
* Keep changes focused on a single issue
* Link the related issue in the PR description
* Provide a clear summary of your changes

Example:

```text
Closes #1

Summary:
- Added CONTRIBUTING.md
- Added contribution workflow documentation
- Linked guide from README
```

## Community

- Website: https://ageos.dev
- Discord: https://discord.gg/skwKqSgvD2
- If you find AgeOS useful, consider starring the repository and joining the community.

Thank you for contributing to AgeOS!
