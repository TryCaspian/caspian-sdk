import hashlib
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiKey, Project


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_project(
    session: Session = Depends(get_session),
    authorization: str = Header(default=""),
) -> Project:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer API key")
    key = authorization.removeprefix("Bearer ").strip()
    api_key = session.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_key(key))
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    project = session.get(Project, api_key.project_id)
    if project is None:  # key outlived its project — treat as invalid, don't 500
        raise HTTPException(status_code=401, detail="Invalid API key")
    return project
