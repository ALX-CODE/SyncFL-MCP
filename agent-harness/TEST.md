# Harness verification

From the repository's prepared environment:

```powershell
python -m pytest agent-harness/cli_anything/flstudio/tests -q
```

These tests use fake transport unless a case explicitly requires a live host. They do not qualify every CLI command against a running FL instance. Follow the [development guide](../docs/DEVELOPMENT.md) and use a disposable project for live checks.
