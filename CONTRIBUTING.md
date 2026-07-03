# Contributing to BubbleHub

Thank you for your interest in contributing to BubbleHub!

## Getting Started

1. Fork the repository to your GitHub account.
2. Clone your fork locally:

```bash
git clone https://github.com/<your-username>/BubbleHub.git
cd BubbleHub
```

3. Add the upstream repository:

```bash
git remote add upstream https://github.com/bublhub/BubbleHub.git
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

CI runs libbubble C unit tests and Python unit tests together:

```bash
docker build -f docker/Dockerfile --target unit-test -t bubblehub:unit .
docker run --rm --privileged --security-opt seccomp=unconfined bubblehub:unit
```

To run only the libbubble Meson tests locally:

```bash
meson setup libbubble/build libbubble --prefix=/usr/local
meson compile -C libbubble/build
meson test -C libbubble/build --print-errorlogs
```

C tests live under `libbubble/tests/` and link against the built `libbubble.so`. Mount-related overfs tests require privileges and skip automatically in unprivileged environments; CI runs them inside the privileged Docker unit-test image.

### Coverage

CI uploads Python unit coverage, unit `libbubble` C coverage, and integration-driven `libbubble` C coverage to [Codecov](https://codecov.io/gh/bublhub/BubbleHub). The project target is 45% line coverage (see `codecov.yml`). Codecov reporting runs after both unit and integration coverage artifacts are available so PR comments and merge-status coverage checks use the complete report set. To reproduce the unit coverage run locally:

```bash
docker build -f docker/Dockerfile --target unit-test \
  --build-arg MESON_COVERAGE=true -t bubblehub:unit-cov .
mkdir -p .ci-artifacts/coverage
docker run --rm --privileged --security-opt seccomp=unconfined \
  -v "$PWD/.ci-artifacts/coverage:/coverage-out" \
  bubblehub:unit-cov scripts/ci/run-unit-tests-coverage.sh
```

HTML reports are written under `.ci-artifacts/coverage/`. CI also keeps those reports as workflow artifacts; Codecov provides the dashboard, PR comments, and README badge. Integration coverage is collected in CI with `scripts/ci/run-integration-tests-coverage.sh`; use the regular integration test command below for local validation unless you are specifically debugging coverage collection.

### Integration Tests

```bash
docker volume create bubblehub-cache-local
docker volume create bubblehub-openclaw-local

docker build -f docker/Dockerfile --target integration-test -t bubblehub:integration .

docker run --rm --privileged --security-opt seccomp=unconfined \
  -v bubblehub-cache-local:/cache/bubblehub \
  -v bubblehub-openclaw-local:/cache/openclaw \
  bubblehub:integration
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

- Website: https://BubbleHub.ai
- Discord: https://discord.gg/skwKqSgvD2
- If you find BubbleHub useful, consider starring the repository and joining the community.

Thank you for contributing to BubbleHub!
