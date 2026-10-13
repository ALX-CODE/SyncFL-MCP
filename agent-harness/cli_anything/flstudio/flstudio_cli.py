"""One-shot JSON commands and persistent interactive REPL."""

import asyncio
import json
import shlex
from pathlib import Path

import click

from .core import session
from .utils.flstudio_backend import call


def emit(result):
    click.echo(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    if isinstance(result, dict) and result.get("success") is False:
        raise click.exceptions.Exit(1)


def parse_json(value):
    try:
        return json.loads(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


class JSONGroup(click.Group):
    """Preserve machine-readable failures, including GUI and filesystem errors."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit:
            raise
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            emit({"success": False, "error": str(exc), "error_type": type(exc).__name__})


@click.group(invoke_without_command=True, cls=JSONGroup)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="JSON output (also the default for one-shot commands).",
)
@click.pass_context
def cli(ctx, json_output):
    """Control real FL Studio. Mixer/channel/slot indices are zero-based."""
    if ctx.invoked_subcommand is None:
        click.echo("FL Studio REPL. help lists commands; exit leaves FL running.")
        while True:
            try:
                line = input("fl> ").strip()
                if line in ("exit", "quit"):
                    return
                if line:
                    cli.main(
                        args=["--help"] if line == "help" else shlex.split(line),
                        standalone_mode=False,
                    )
            except (EOFError, KeyboardInterrupt):
                return
            except (click.ClickException, click.exceptions.Exit) as exc:
                click.echo(str(exc), err=True)


@cli.command()
def health():
    """Verify live controller handshake, not merely an open MIDI port."""
    emit(call("bridge.ping"))


@cli.command()
@click.option("--output", type=click.Path(path_type=Path))
def capabilities(output):
    result = call("bridge.capabilities")
    if output:
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        emit(
            {
                "success": result.get("success"),
                "output": str(output),
                "native_count": len(result.get("native", {})),
            }
        )
    else:
        emit(result)


@cli.command()
@click.argument("action")
@click.argument("params", default="{}")
@click.option("--timeout", default=20.0, type=click.FloatRange(0.1, 60))
def command(action, params, timeout):
    """Send a typed or legacy action with a JSON parameter object."""
    emit(call(action, parse_json(params), timeout))


@cli.command()
@click.argument("function")
@click.argument("args", default="[]")
@click.option("--write", is_flag=True)
def native(function, args, write):
    """Use an advertised native signature. Explicit --write permits mutation."""
    emit(
        call("native.call", {"function": function, "args": parse_json(args), "allow_write": write})
    )


@cli.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--apply", is_flag=True, help="Execute; omitted means preflight only.")
def batch(file, apply):
    data = json.loads(file.read_text(encoding="utf-8-sig"))
    emit(
        call(
            "bridge.batch",
            {"steps": data if isinstance(data, list) else data["steps"], "dry_run": not apply},
            45,
        )
    )


@cli.command()
@click.argument("file", type=click.Path(path_type=Path))
def snapshot(file):
    result = call("project.snapshot", timeout=30)
    if result.get("success"):
        file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        emit(
            {
                "success": True,
                "file": str(file.resolve()),
                "channels": len(result["channels"]),
                "tracks": len(result["mixer"]),
            }
        )
    else:
        emit(result)


@cli.command()
@click.argument("track", type=click.IntRange(0))
@click.option("--slot", type=click.IntRange(0, 9), default=0)
def select(track, slot):
    result = call(
        "native.call", {"function": "mixer.setActiveTrack", "args": [track], "allow_write": True}
    )
    if result.get("success"):
        session.save({"track": track, "slot": slot})
    emit(result)


@cli.command()
@click.option("--track", type=int)
@click.option("--slot", type=click.IntRange(0, 9))
@click.option("--query", default="")
@click.option("--offset", default=0, type=click.IntRange(0))
@click.option("--limit", default=100, type=click.IntRange(1, 500))
def params(track, slot, query, offset, limit):
    current = session.load()
    emit(
        call(
            "plugins.parameters",
            {
                "track": track if track is not None else current["track"],
                "slot": slot if slot is not None else current["slot"],
                "query": query,
                "offset": offset,
                "limit": limit,
            },
        )
    )


@cli.command()
def undo():
    """Use FL's actual undo history; scripted operations may not all be covered."""
    emit(call("session.undo"))


@cli.command()
def redo():
    emit(call("session.redo"))


@cli.command()
@click.argument("request_id")
def receipt(request_id):
    emit(call("bridge.receipt", {"id": request_id}))


@cli.command("plugin-add")
@click.argument("track", type=click.IntRange(1))
@click.argument("slot", type=click.IntRange(0, 9))
@click.argument("name")
@click.option("--row", default=0, type=click.IntRange(0, 19))
def plugin_add(track, slot, name, row):
    """Load an effect through Windows MCP, then verify native slot identity."""
    from fl_studio_mcp.utils.windows_workflows import run_workflow

    emit(asyncio.run(run_workflow("add_plugin", track=track, slot=slot, name=name, row=row)))


@cli.command("plugin-remove")
@click.argument("track", type=click.IntRange(1))
@click.argument("slot", type=click.IntRange(0, 9))
@click.argument("expected_plugin")
def plugin_remove(track, slot, expected_plugin):
    from fl_studio_mcp.utils.windows_workflows import run_workflow

    emit(
        asyncio.run(
            run_workflow("remove_plugin", track=track, slot=slot, expected_plugin=expected_plugin)
        )
    )


@cli.command("ensure-tracks")
@click.argument("last_insert", type=click.IntRange(1, 125))
def ensure_tracks(last_insert):
    """Append the missing mixer inserts through FL's Add multiple command."""
    from fl_studio_mcp.utils.windows_workflows import run_workflow

    emit(asyncio.run(run_workflow("ensure_tracks", last_insert=last_insert)))


@cli.command("plugin-chain")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--receipt-file", type=click.Path(path_type=Path))
def plugin_chain(file, receipt_file):
    """Load a JSON list of {track,slot,name,wet?} using one Windows MCP session."""
    from fl_studio_mcp.utils.windows_workflows import run_workflow

    emit(
        asyncio.run(
            run_workflow(
                "load_chain",
                items=json.loads(file.read_text()),
                receipt_file=str(receipt_file) if receipt_file else None,
            )
        )
    )


@cli.command("project-save")
@click.argument("expected_path", type=click.Path(exists=True, path_type=Path))
def project_save(expected_path):
    """Save the already named FLP; verify the exact expected file changed."""
    from fl_studio_mcp.utils.windows_workflows import run_workflow

    emit(asyncio.run(run_workflow("save_project", expected_path=str(expected_path))))


@cli.command("parameter-value")
@click.argument("track", type=click.IntRange(0))
@click.argument("slot", type=click.IntRange(0, 9))
@click.argument("parameter", type=click.IntRange(0))
@click.argument("plugin")
@click.argument("name")
@click.argument("target", type=float)
@click.option("--unit", required=True, help="Exact displayed unit, such as ms, sec or dB.")
@click.option("--tolerance", default=0.01, type=float)
def parameter_value(track, slot, parameter, plugin, name, target, unit, tolerance):
    """Calibrate a continuous control in displayed units; transport must be stopped."""
    from fl_studio_mcp.utils.parameter_values import set_display_value

    emit(set_display_value(track, slot, parameter, plugin, name, target, unit, tolerance))


@cli.command("mix-profile")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--apply", is_flag=True)
@click.option("--receipt-file", type=click.Path(path_type=Path))
def mix_profile(file, apply, receipt_file):
    """Preflight or apply guarded named controls with physical units and restore data."""
    from fl_studio_mcp.utils.mix_profiles import apply_profile

    emit(
        apply_profile(
            json.loads(file.read_text(encoding="utf-8-sig")), apply=apply, receipt_file=receipt_file
        )
    )


if __name__ == "__main__":
    cli()
