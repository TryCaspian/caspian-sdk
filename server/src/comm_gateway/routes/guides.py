"""Per-channel behaviour guides for the agent's brain (see channel_guides.py).

- GET /v1/channels/{channel}/guide  — one channel's guide (public, generic docs).
- GET /v1/behavior-prompt           — combined guide for THIS project's active
  channels (auth), ready to inject into the agent's system prompt.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_project, get_session
from ..channel_guides import combined_guide, guide_for
from ..models import Connection, Project

router = APIRouter(prefix="/v1")


@router.get("/channels/{channel}/guide", response_class=PlainTextResponse)
def channel_guide(channel: str) -> str:
    guide = guide_for(channel)
    if guide is None:
        raise HTTPException(status_code=404, detail=f"No behaviour guide for channel {channel!r}")
    return guide


@router.get("/behavior-prompt", response_class=PlainTextResponse)
def behavior_prompt(
    project: Project = Depends(get_project),
    session: Session = Depends(get_session),
) -> str:
    channels = list(
        session.execute(
            select(Connection.channel)
            .where(Connection.project_id == project.id, Connection.status == "active")
            .distinct()
        ).scalars()
    )
    return combined_guide(channels)
