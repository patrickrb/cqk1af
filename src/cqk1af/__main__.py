from __future__ import annotations

import asyncio
import sys

import click

from .config import load_settings
from .util.logging import configure_logging, get_logger


@click.group()
@click.option("--log-level", default=None, help="Override log level (DEBUG/INFO/WARN/ERROR)")
@click.option("--log-json/--log-pretty", default=None, help="JSON logs vs. pretty console")
@click.pass_context
def cli(ctx: click.Context, log_level: str | None, log_json: bool | None) -> None:
    """CQK1AF — FlexRadio SSB voice assistant."""
    settings = load_settings()
    effective_level = log_level or settings.log_level
    effective_json = settings.log_json if log_json is None else log_json
    configure_logging(level=effective_level, json=effective_json)
    ctx.obj = settings


@cli.command()
@click.pass_obj
def hello(settings) -> None:  # noqa: ANN001
    """Smoke test: prints the loaded operator callsign and exits."""
    log = get_logger("hello")
    log.info(
        "cqk1af.ready",
        callsign=settings.operator.callsign,
        license_class=settings.operator.license_class,
        mock_radio=settings.radio.mock_mode,
    )


@cli.command()
@click.option("--mock-radio/--real-radio", default=None, help="Force radio mode")
@click.pass_obj
def demo(settings, mock_radio: bool | None) -> None:  # noqa: ANN001
    """Run a scripted mock session through the state machine."""
    from .demo import demo_main

    if mock_radio is not None:
        settings.radio.mock_mode = mock_radio
    rc = asyncio.run(demo_main(settings))
    sys.exit(rc)


@cli.command()
@click.option(
    "--transport",
    type=click.Choice(["streamable-http", "sse", "stdio"]),
    default="streamable-http",
)
@click.option("--no-dashboard", is_flag=True, help="Skip the dashboard backend")
@click.option("--no-mcp", is_flag=True, help="Skip the MCP server")
@click.pass_obj
def serve(settings, transport: str, no_dashboard: bool, no_mcp: bool) -> None:  # noqa: ANN001
    """Run the MCP server and the dashboard backend."""
    from .app import App
    from .dashboard.api import run_dashboard_server
    from .mcp_server.server import run_mcp_server

    app = App.build(settings)
    log = get_logger("serve")

    async def _run() -> None:
        tasks: list[asyncio.Task] = []
        if not no_mcp:
            log.info(
                "mcp.server.start",
                transport=transport,
                host=settings.mcp.host,
                port=settings.mcp.port,
            )
            tasks.append(asyncio.create_task(run_mcp_server(app, transport=transport), name="mcp"))
        if not no_dashboard:
            log.info(
                "dashboard.start",
                host=settings.dashboard.host,
                port=settings.dashboard.port,
            )
            tasks.append(asyncio.create_task(run_dashboard_server(app), name="dashboard"))
        if not tasks:
            log.warning("serve.nothing_to_run")
            return
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await app.shutdown()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("serve.interrupted")


def main() -> None:
    # Click's standard entry. asyncio loops are spun up inside subcommands.
    cli(prog_name="cqk1af")


if __name__ == "__main__":
    main()
