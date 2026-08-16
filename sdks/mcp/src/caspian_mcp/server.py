from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from caspian_mcp.http import run_http
from caspian_mcp.inbox import Inbox
from caspian_mcp.privacy.guard import Guard
from caspian_mcp.privacy.types import MappingExpired, SanitizeResult
from caspian_mcp.session import SessionGuard

load_dotenv(Path.cwd() / ".env", override=True)

guard = Guard()
session = SessionGuard(guard)
_inbox: Inbox | None = None

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp 2.x
    from mcp.server import MCPServer as FastMCP  # type: ignore[assignment]

mcp = FastMCP("caspian")


def _client():
    from caspian_sdk import CommClient

    return CommClient()


def _inbox_tools() -> Inbox:
    global _inbox
    if _inbox is None:
        _inbox = Inbox(_client(), session)
    return _inbox


@mcp.tool()
def sanitize(text: str) -> dict[str, str]:
    """Replace Sensitive Spans with typed Placeholders. Returns Safe Text and a Mapping Id."""
    result: SanitizeResult = session.sanitize(text)
    return {"safe_text": result.safe_text, "mapping_id": result.mapping_id}


@mcp.tool()
def restore(text: str, mapping_id: str) -> dict[str, str]:
    """Substitute real values back into text that still contains Placeholders."""
    try:
        restored = session.restore(text, mapping_id)
    except MappingExpired as exc:
        return {"restored_text": "", "error": f"mapping expired: {exc}"}
    return {"restored_text": restored}


@mcp.tool()
def redaction_report(mapping_id: str) -> dict[str, int | str]:
    """Counts of unique Placeholders per Category for a Mapping Id. Never returns real values."""
    try:
        return session.redaction_report(mapping_id)
    except MappingExpired as exc:
        return {"error": f"mapping expired: {exc}"}


@mcp.tool()
def list_inbox(limit: int = 20) -> dict:
    """Conversations plus a sanitized last-message preview. Never returns raw bodies."""
    return _inbox_tools().list_inbox(limit=limit)


@mcp.tool()
def get_thread(conversation_id: str, limit: int = 50, backfill: bool = False) -> dict:
    """Sanitized transcript for one conversation. Optional Caspian backfill first."""
    return _inbox_tools().get_thread(conversation_id, limit=limit, backfill=backfill)


@mcp.tool()
def brief_status(n: int = 5, m: int = 20) -> dict:
    """Sanitized digest of the newest n conversations × last m messages. No Completer call."""
    return _inbox_tools().brief_status(n=n, m=m)


def main() -> None:
    parser = argparse.ArgumentParser(description="Caspian MCP (stdio or private HTTP)")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Streamable HTTP on 127.0.0.1 (needs MCP_AUTH_TOKEN)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        run_http(mcp, host=args.host, port=args.port)
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
