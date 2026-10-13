# Repository maintenance

SyncFL is developed in [ALX-CODE/SyncFL-MCP](https://github.com/ALX-CODE/SyncFL-MCP).
The public repository uses the following settings as of October 12, 2026.
GitHub settings can change independently of this source file.

## Contribution workflow

Work on a feature branch and open a pull request to `main`. The main-branch
rules require a pull request, resolved review conversations, linear history,
and the five SyncFL checks against the current base:

- `test (3.12)`, `test (3.13)`, and `test (3.14)`
- `native-midi`
- `rust-tempo`

Human approval is optional so either maintainer can ship a checked change without
waiting for the other. CODEOWNERS requests review from ALX-CODE and nobibasedd;
it does not grant repository access. No bypass actors are configured. Changes
are squash-merged using the pull request title and body. Automatic merging is
available when explicitly selected on a pull request; it still waits for the
rules to pass. Merged feature branches are deleted automatically.

Force pushes and deletion of `main` are blocked. Version tags matching `v*`
cannot be updated or deleted. Publish a new version to correct a release; do
not move an existing release tag. Repository administrators can edit rules,
so these controls prevent ordinary mistakes rather than making history immutable.

## Security and dependency checks

- Secret scanning and push protection check supported credential patterns.
- Dependabot alerts and security updates cover supported dependency manifests.
- Weekly GitHub Actions version updates are grouped into one pull request.
- CodeQL default setup analyzes detected supported languages, with local and
  remote sources included in its threat model. Results appear in the
  [Security tab](https://github.com/ALX-CODE/SyncFL-MCP/security).
- Actions use read-only default token permissions, cannot approve pull requests,
  and the test workflow pins third-party actions to commit hashes.

Automated checks do not establish that code is safe, that every dependency is
covered, or that FL Studio behavior has been live-qualified. Follow
[the security policy](../SECURITY.md) for private reports and
[validation guidance](VALIDATION.md) for evidence requirements.

## Community and releases

Use [Discussions](https://github.com/ALX-CODE/SyncFL-MCP/discussions) for questions,
workflow help and demos. The issue chooser separates bug reports, feature
requests and private security reporting. Release-note categories live in
[`.github/release.yml`](../.github/release.yml); generating notes does not publish
a release automatically. Release archives and checksums are under
[Releases](https://github.com/ALX-CODE/SyncFL-MCP/releases).

## Branding

The repository social preview is
[`assets/syncfl-social-preview.jpg`](assets/syncfl-social-preview.jpg).
Its capability counts describe the current documented inventory; update the
artwork when those counts change. The supplied logo remains unchanged.
See [asset provenance](assets/README.md) before replacing either file.
