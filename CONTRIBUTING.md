# Development and Testing

## Primary Development Commands

To check and resolve linting issues in the codebase, run:

```console
uv run ruff check --fix
```

To check and resolve formatting issues in the codebase, run:

```console
uv run ruff format
```

To check the unit tests in the codebase, run:

```console
uv run pytest
```

To check the typing in the codebase, run:

```console
uv run mypy
```

To generate a code coverage report after testing locally, run:

```console
uv run coverage html
```

To check the lock file is up to date:

```console
uv lock --check
```

## Shortcut Task Commands

###### For Running Individual Checks

```console
uv run poe check-lock
uv run poe check-format
uv run poe check-lint
uv run poe check-tests
uv run poe check-typing
```

###### For Running All Checks

```console
uv run poe check-all
```

###### For Running Individual Fixes

```console
uv run poe fix-format
uv run poe fix-lint
```

###### For Running All Fixes

```console
uv run poe fix-all
```

###### For Running All Fixes and Checks

```console
uv run poe fix-and-check-all
```

## Online Unit Tests

This project includes unit tests which require authenticated access to the Fulcrum Genomics Latch workspace.

These tests are configured with the appropriate authentication in the GitHub Actions "Code Checks" workflow.

If you would like to be able to run these tests locally, request access to the Fulcrum Genomics Latch workspace from Nils Homer. 
Then, before running the unit test suite, ensure you are logged in to Latch and the Fulcrum Genomics workspace is active.

First, log in via `latch login`.
This will open a browser pop-up with a login prompt.
Log in with Google SSO.

```console
$ uv run latch login
```

Then, activate the Fulcrum workspace with `latch workspace`.

```console
$ uv run latch workspace
Select Workspace

    User's Default Workspace
    Client1
  > Fulcrum Genomics (currently selected)
    Client2
    Client3

[ARROW-KEYS] Navigate	[ENTER] Select	[Q] Quit
```

## Creating a Release on PyPI

> [!NOTE]
> This project follows [Semantic Versioning](https://semver.org/).
> In brief:
> 
> - `MAJOR` version when you make incompatible API changes
> - `MINOR` version when you add functionality in a backwards compatible manner
> - `PATCH` version when you make backwards compatible bug fixes

1. Decide on the new version number to be released.
   - Go to the [latest release](https://github.com/fulcrumgenomics/fgpyo/releases/latest) and see what's been committed since then!
   - Pick the appropriate new SemVer version number.
2. Clone the repository and ensure you are on the `main` branch.
3. Checkout a new branch to prepare the library for release, e.g.:
    ```console
    git checkout -b ms_release-0.2.0 
    ```
4. Bump the version of the library to the desired SemVer, e.g.:
    ```console
    uv version --bump minor
    ```
5. Commit the updated version with a release-scoped `chore` message, e.g.:
   ```console
   git commit -m "chore(release): bump to 0.2.0"
   ```
9. Push the commit to the upstream remote, open a PR, ensure tests pass, and seek reviews.
    - **IMPORTANT:** When opening the PR, also prefix the PR title with `chore(release):` to ensure the merged commit has an appropriate message. (This should happen automatically if the PR includes only one commit.)
10. Squash merge the PR.
11. Tag the new commit on the main branch of the origin repository with the new SemVer version number.

GitHub Actions will take care of the remainder of the deployment and release process:

1. Unit tests will be run again for safety's sake.
2. A source distribution will be built.
3. Multi-arch multi-Python binary distributions will be built.
4. Assets will be deployed to PyPI with the new SemVer.
5. A [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/)-aware changelog will be drafted.
6. A GitHub release will be created with the new SemVer and the drafted changelog.

> [!WARNING]
> Consider editing the changelog if there are any errors or necessary enhancements.

