# Contributing

Please follow the procedure below:

1. Please search existing issues to determine if an issue already exists for what you intend to contribute. 
2. If the issue does not already exist, create a new issue and describe the bug or feature request.
3. Please wait for a feedback.
4. With positive feedback from us, fork the repository and prepare your changes there.
5. Open a pull request to `netcloud/aciClient`.

## Pull Request Process

1. Install the development environment:

```bash
uv sync --group dev
```

2. Run the local checks before opening or updating a pull request:

```bash
uv run pytest
uv run ruff check .
```

3. Update documentation and examples when your change affects behavior.

4. Open a pull request to `netcloud/aciClient`.

The upstream CI workflow runs on pull requests and validates the change set.

## Release Process

Releases are done from the upstream repository:

1. Contributors work in forks and submit pull requests to `netcloud/aciClient`.
2. The upstream CI workflow runs tests and lint checks for the pull request.
3. After review, the pull request is merged into `netcloud/aciClient`.
4. When maintainers want to publish a release, they update the version in `pyproject.toml` if needed.
5. Maintainers create a version tag such as `v2.0.0` in `netcloud/aciClient`.
6. The upstream publish workflow builds the package with `uv build` and publishes it to PyPI.

Publishing requires PyPI trusted publishing to be configured for `netcloud/aciClient`.

# Coding Convention

* Python Language Rules (PEP8) are followed and verified with Ruff
* The code is structured according to the Clean Code paradigm
* Code and Documentation is written in English
* At least UnitTests are written
* A useful exception handling is available
* A useful logging is available
* If foreign code is used, no license agreements have been broken.

## Local Development

Tests in `test/` are grouped by behavior: client configuration, authentication,
gateway routing, responses, pagination, snapshots, subscriptions, and consumer
test helpers. Shared factories live in `test/helpers.py`; `test/test_aci.py`
covers the legacy `ACI` interface. Add tests to the relevant behavior module.

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```
