# Preparing a SyncFL public release

Canonical repository: **[ALX-CODE/SyncFL-MCP](https://github.com/ALX-CODE/SyncFL-MCP)**. Public name: **SyncFL**. Maintainers: **[nobibasedd](https://github.com/nobibasedd)** and **[ALX-CODE](https://github.com/ALX-CODE)**.

This repository starts from a reviewed source-only snapshot with fresh Git history. Earlier private development commits, song projects and reference media are not included. Keep production material outside this repository. **Never merge private development history or copy its `.git` directory into SyncFL.** Inherited copyright and license notices remain with the source.

## Create a source-only snapshot

After tests and documentation checks pass, commit the intended source revision and run:

```powershell
python scripts/export_public_release.py --output D:\Releases\SyncFL-MCP `
  --archive D:\Releases\SyncFL-MCP.zip
```

Both destinations must be new. The exporter requires a clean tracked tree by default, copies only tracked allowlisted files, excludes production projects/reference media and `.git`, checks known credential/personal-path patterns, validates bundled runtime hashes, and writes `PUBLIC-MANIFEST.json` with source commit and per-file hashes. It never changes source files or publishes anything.

The content check includes ASCII and UTF-16 strings inside native binaries and reviewed assets. It rejects personal home paths from Windows, macOS and Linux, known token formats, and URLs carrying access tokens. A binary hash proves integrity; it does not replace this privacy check. Compressed presets also receive a decoded-content review and must retain their approved hashes.

`--allow-dirty` exists for local review builds. Such manifests explicitly report a dirty source tree and should not be treated as a committed release. The checker is a targeted publication check, not a guarantee that no sensitive content can exist.

## Review before publication

1. Inspect the README, maintainer credits, notices, capability claims and exported file list.
2. Run setup/tests from the exported source on a clean environment. No private project or reference corpus should be necessary.
3. Confirm the intended repository visibility, collaborator access and support contact. Keep inherited copyright/license notices.
4. Use only self-created or explicitly redistributable demonstration media. The private song/demo assets are not a public demo package.
5. Publish the reviewed source revision or snapshot only after the maintainers authorize publication. Creating a private repository does not authorize making it public.

Review every reachable Git commit and tag as well as the current files: deleting sensitive data in a later commit does not remove it from earlier revisions. Keep any pre-cleanup backups outside the repository and release ZIP. Public maintainer handles and required upstream copyright notices are intentional credits, not private contact information.

When bringing changes from a separate private development checkout, review and copy source files into a normal SyncFL commit. Do not add a private-history remote or merge those branches.

## GitHub presentation

Description: **SyncFL connects AI assistants to FL Studio with native MIDI, named plugin controls, composition, mixing, audio analysis and verified workflows.**

Topics: `mcp`, `fl-studio`, `midi`, `music-production`, `daw`, `python`, `audio`, `ai-tools`.

Review issue settings, private vulnerability reporting and collaborator roles before launch. README badges and package metadata use the canonical SyncFL URL. A source tag should identify the exact tested snapshot; keep experimental capabilities labeled as such.

The source-only snapshot includes public guides and generated tool schemas. Private session logs, external artist MIDI, song projects, demo video and original Git history are deliberately absent. Native runtime binaries remain with pinned manifests and their redistribution notices.
