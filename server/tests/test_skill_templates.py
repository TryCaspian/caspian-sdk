"""The skill fork and the framework scaffolds it serves.

The consumers are coding agents that write files verbatim from these
documents, so the tests hold the documents to code standards: every embedded
Python file must parse, every spoke must carry the invariants the design
depends on, and the one Python foot-gun (a caspian/__init__.py shadowing the
SDK) must be warned against everywhere it could happen.
"""

from __future__ import annotations

import ast
import re

from comm_gateway.skill_templates import (
    FORK_SECTION,
    PY_CASPIAN_BOT,
    SPOKES,
    TS_CASPIAN_CONNECTIONS,
    TS_CASPIAN_HANDLERS,
    TS_CASPIAN_INDEX,
)


def test_hub_opens_with_the_fork(client) -> None:
    body = client.get("/SKILL.md").text
    assert "Two ways to use this guide" in body
    assert "/SKILL/openai-agents-python.md" in body
    # the fork must come before the wire-in instructions it forks from
    assert body.index("Two ways") < body.index("## How to drive this")


def test_every_spoke_serves_with_base_url_substituted(client) -> None:
    for slug in SPOKES:
        response = client.get(f"/SKILL/{slug}.md")
        assert response.status_code == 200, slug
        assert "{BASE_URL}" not in response.text, slug
        assert "/v1/projects/sandbox" in response.text, slug


def test_unknown_slug_is_404(client) -> None:
    assert client.get("/SKILL/rails.md").status_code == 404


def test_no_spoke_teaches_the_legacy_api() -> None:
    for slug, text in SPOKES.items():
        assert "CommClient" not in text, slug
        assert "connect_email" not in text, slug


def test_python_spokes_warn_about_init_py() -> None:
    """A coding agent's instinct is to add __init__.py; both the document and
    the scaffold file itself must say not to."""
    for slug, text in SPOKES.items():
        if slug.endswith("-python"):
            assert "__init__.py" in text, slug
    assert "__init__.py" in PY_CASPIAN_BOT


def _python_blocks(markdown: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", markdown, re.S)


def test_every_embedded_python_file_parses() -> None:
    for slug, text in SPOKES.items():
        if not slug.endswith("-python"):
            continue
        blocks = _python_blocks(text)
        assert len(blocks) >= 2, f"{slug}: expected agent.py and caspian/bot.py"
        for block in blocks:
            ast.parse(block)  # raises on syntax errors


def test_the_seam_is_clean() -> None:
    """Framework imports never inside caspian/, Caspian imports never in the
    brain. This is the whole maintainability argument of the design."""
    assert "from agent import ask" in PY_CASPIAN_BOT
    for ts in (TS_CASPIAN_CONNECTIONS, TS_CASPIAN_HANDLERS, TS_CASPIAN_INDEX):
        assert "@openai/agents" not in ts
        assert "langchain" not in ts
    for slug, meta_text in SPOKES.items():
        agent_block = meta_text.split("## 3.")[0]
        # the brain section must not import the SDK
        if slug.endswith("-python"):
            assert "from caspian import" not in _python_blocks(agent_block)[0], slug


def test_fork_lists_every_spoke_exactly() -> None:
    listed = set(re.findall(r"/SKILL/([a-z-]+)\.md", FORK_SECTION))
    assert listed == set(SPOKES), listed ^ set(SPOKES)


def test_coding_agent_spokes_carry_the_security_banner() -> None:
    """A messaging channel wired to a tool-wielding CLI is RCE-by-DM; every
    such scaffold must warn and must ship the allowlist on."""
    for slug in ("claude-code-python", "codex-python", "opencode-python"):
        assert "SECURITY - read this" in SPOKES[slug], slug
        assert "CASPIAN_ALLOWED_SENDERS=you@example.com" in SPOKES[slug], slug
    # ordinary framework spokes must NOT carry it (noise)
    assert "SECURITY - read this" not in SPOKES["openai-agents-python"]
