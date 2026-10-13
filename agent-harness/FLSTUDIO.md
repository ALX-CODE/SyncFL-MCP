# CLI architecture

This optional harness exposes a command-line interface to the same FL Studio transport. The current [architecture](../docs/ARCHITECTURE.md) and [capability matrix](../docs/CAPABILITIES.md) describe the maintained server; historical tool counts are not a compatibility contract.

CLI operations still follow the server's background policy and current target requirements. A direct command is not an alternative route around identity, revision or input-mode checks. Use `--help` to discover the installed command surface.
