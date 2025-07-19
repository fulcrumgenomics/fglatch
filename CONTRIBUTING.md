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


