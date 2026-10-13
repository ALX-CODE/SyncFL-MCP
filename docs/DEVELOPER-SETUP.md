# Developer setup

Use this setup for contributors, including [nobibasedd](https://github.com/nobibasedd).
It creates a working Git checkout and an editable Python environment. An FL Studio
installation is optional for offline development and tests.

| Setup | Command | Includes |
|---|---|---|
| Use SyncFL | `Install.cmd` | Runtime dependencies, FL integration, optional assistant registration |
| Contribute to SyncFL | `Develop.cmd` | Git checkout, runtime and development dependencies, editable CLI |
| Add the CLI to a runtime installation | `install.ps1 -Cli` | Runtime setup plus the `syncfl` command |

## From a downloaded ZIP

1. Install [Git for Windows](https://gitforwindows.org/).
2. The public repository can be cloned without a GitHub sign-in. To submit
   changes, fork it using your own GitHub account; the normal Git credential
   manager handles sign-in when pushing. Never put tokens in a script or URL.
3. Download and extract the repository ZIP, then double-click **`Develop.cmd`**.

The bootstrap clones into `source\SyncFL-MCP` under your user profile. If that
folder is already a checkout of this repository, it reuses it. When run from an
existing SyncFL checkout, it uses that checkout by default. It preserves your
branch and local changes; it does not fetch, pull, reset or switch branches.

To choose a folder, use PowerShell from the extracted source directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_dev.ps1 `
  -Destination 'D:\Code\SyncFL-MCP'
```

`-Plan` shows the proposed checkout and installation without cloning, downloading
or editing files. Setup refuses unrelated repositories and nonempty folders that
are not Git checkouts. It requires Git; a missing uv installation is handled by
the same checksum-verified bootstrap as the normal installer.

## From an existing clone

```powershell
git clone https://github.com/ALX-CODE/SyncFL-MCP.git
cd SyncFL-MCP
.\Develop.cmd
```

The developer environment uses Python 3.13 by default, locked audio/Serum
dependencies, pytest, Ruff, packaging tools and FL API stubs. The main package
and CLI are editable: source changes take effect in new Python processes.
Restart a running MCP server after changing its code.

Developer setup defaults to **no FL deployment and no assistant configuration**.
It does not start FL or touch keyboard/mouse input. To opt into installing the FL
integration and registering a client:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_dev.ps1 `
  -InstallFlIntegration -Client codex
```

For a custom FL data root, use the normal installer from the resulting checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -Development -Client codex -FlUserData 'D:\Music\Image-Line'
```

## Use the CLI

From the resulting checkout:

```powershell
.\.venv\Scripts\syncfl.exe --help
```

Or activate the environment once:

```powershell
.\.venv\Scripts\Activate.ps1
syncfl --help
```

`syncfl` is the branded alias for the existing CLI. The
`cli-anything-flstudio` command remains available. Help is offline; live commands
need the FL integration and a connected session. For example, after configuring
FL, `syncfl --json health` checks the controller handshake. Use command help
before mutation; the CLI shares the server's background policy and guards.

## Make and test a change

Choose your own branch name; setup leaves the current branch unchanged:

Before your first commit, check `git config user.name` and `git config user.email`.
If missing, set them locally in this checkout. Use the public noreply address
from [GitHub email settings](https://github.com/settings/emails) if you want to
keep your personal email out of commits. Setup never changes your Git identity.

```powershell
git switch -c feature/my-change
.\.venv\Scripts\python.exe -m pytest tests agent-harness/cli_anything/flstudio/tests -q
.\.venv\Scripts\python.exe scripts/generate_tool_docs.py --check
.\.venv\Scripts\python.exe scripts/check_public_docs.py
git diff --check
```

Add `-RunTests` to `setup_dev.ps1` to run the offline Python and CLI suites during
setup. Native C++ and Rust development needs their separate toolchains; this
bootstrap uses the bundled runtimes and does not install Visual Studio or Rust.
See [development](DEVELOPMENT.md) for code checks and
[contributing](../CONTRIBUTING.md) for pull requests. Keep personal settings,
credentials, projects and calibration recordings outside the repository.

## Submit from a fork

The developer bootstrap checks that `origin` points to the main repository.
Keep that remote so setup can reuse your checkout. Contributors without write
access should create a fork on GitHub and add it as a separate remote after
running `Develop.cmd`:

```powershell
git remote add fork https://github.com/YOUR-USERNAME/SyncFL-MCP.git
git push -u fork feature/my-change
```

Replace `YOUR-USERNAME` and `feature/my-change` with your account and branch.
Open the pull request from your fork against `ALX-CODE/SyncFL-MCP`'s `main`
branch. Maintainers with write access can push feature branches directly to
`origin` instead.
