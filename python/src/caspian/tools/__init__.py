"""Tools — agent view derived from Command types.

Definitions are built from the Command models' JSON schema, not a parallel
hand-written parameter list. execute() goes through Thread → Commands.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

from pydantic import BaseModel

from caspian.core.commands import (
    Command,
    Edit,
    Initiate,
    Post,
    React,
    Typing,
)
from caspian.core.types import ThreadId
from caspian.facade.thread import Thread


def _unwrap_command_models() -> dict[str, type[BaseModel]]:
    """Map Command tag → model class from the discriminated union."""
    origin = get_origin(Command)
    args = get_args(Command) if origin is not None else ()
    # Annotated[Union[...], Field] → first arg is the union
    union = args[0] if args else Command
    members = get_args(union) or (union,)
    out: dict[str, type[BaseModel]] = {}
    for member in members:
        if isinstance(member, type) and issubclass(member, BaseModel):
            tag = getattr(member, "model_fields", {}).get("tag")
            name = member.__name__
            if tag is not None:
                out[name] = member
    return out


_COMMANDS = _unwrap_command_models()

# Public tool name → (Command class, description). Presets filter this set.
_TOOL_SPEC: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("post_message", Post, "Send a message to the current thread"),
    ("edit_message", Edit, "Edit an existing message"),
    ("add_reaction", React, "React to a message with an emoji"),
    ("start_typing", Typing, "Show typing indicator"),
    ("send_dm", Initiate, "Send a direct message to a user"),
)

_MESSENGER = frozenset({"post_message", "edit_message", "add_reaction", "start_typing"})
_OUTBOUND = _MESSENGER | {"send_dm"}


def _parameters_from(model: type[BaseModel]) -> dict[str, Any]:
    """JSON-schema properties derived from the Command model (minus the tag)."""
    schema = model.model_json_schema()
    props = {
        key: value
        for key, value in schema.get("properties", {}).items()
        if key != "tag"
    }
    required = [key for key in schema.get("required", []) if key != "tag"]
    return {"type": "object", "properties": props, "required": required}


class ToolDefinition:
    """A single tool definition for agent frameworks."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolSet:
    """A set of tools derived from Command types.

    Models address thread_ids, never raw platform chat ids.
    """

    def __init__(self, thread: Thread | None = None, preset: str = "messenger") -> None:
        self._thread = thread
        self._preset = preset

    @property
    def definitions(self) -> list[ToolDefinition]:
        allowed = _OUTBOUND if self._preset == "outbound" else _MESSENGER
        tools: list[ToolDefinition] = []
        for name, model, description in _TOOL_SPEC:
            if name not in allowed:
                continue
            # Refuse to emit a tool whose Command is not in the kernel union.
            if model.__name__ not in _COMMANDS and model not in _COMMANDS.values():
                continue
            tools.append(
                ToolDefinition(
                    name=name,
                    description=description,
                    parameters=_parameters_from(model),
                )
            )
        return tools

    def execute(self, tool_name: str, args: dict[str, Any]) -> list[Command]:
        """Execute a tool call, returning Commands (not platform HTTP)."""
        thread_id = ThreadId(
            args.get("thread_id", "")
            or (str(self._thread.thread_id) if self._thread else "")
        )
        thread = Thread(thread_id=thread_id)

        match tool_name:
            case "post_message":
                thread.post(args["text"])
            case "edit_message":
                thread.edit(args["message_id"], args["text"])
            case "add_reaction":
                thread.react(args["message_id"], args["emoji"])
            case "start_typing":
                thread.typing()
            case "send_dm":
                thread.initiate(args["text"])
            case _:
                pass

        return thread.commands
