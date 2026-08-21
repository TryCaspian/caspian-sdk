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

# messenger: bound to one conversation. The thread is already known, so these
# tools do not ask the model for a thread_id at all.
_MESSENGER = frozenset(
    {"post_message", "edit_message", "add_reaction", "start_typing", "send_dm"}
)
# outbound: no conversation in hand, so the model must name the thread itself.
# Only tools that can meaningfully take a thread_id as an argument belong here;
# editing or reacting needs a message you are already looking at.
_OUTBOUND = frozenset({"post_message", "send_dm"})


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


def _without_thread_id(parameters: dict[str, Any]) -> dict[str, Any]:
    """Drop thread_id from a schema whose thread is already known."""
    props = {k: v for k, v in parameters.get("properties", {}).items() if k != "thread_id"}
    required = [k for k in parameters.get("required", []) if k != "thread_id"]
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

    Which tools exist depends on whether a thread is bound, not only on the
    preset. With a thread, the tools are scoped to that conversation and
    thread_id is dropped from the schema, because asking a model for a value
    that is then overwritten wastes tokens and invites invented ids. Without a
    thread, only the tools that can meaningfully name their own target are
    offered, so a tool that cannot work is never handed to the model.
    """

    def __init__(self, thread: Thread | None = None, preset: str = "messenger") -> None:
        self._thread = thread
        # Without a thread there is nothing to bind to, so messenger collapses
        # into outbound regardless of what was asked for.
        self._preset = "outbound" if thread is None else preset

    @property
    def bound(self) -> bool:
        """True when the tools are scoped to a thread and hide thread_id."""
        return self._thread is not None and self._preset != "outbound"

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
            parameters = _parameters_from(model)
            if self.bound and name != "send_dm":
                # send_dm names a different conversation by definition, so it
                # keeps its thread_id even when the set is bound.
                parameters = _without_thread_id(parameters)
            tools.append(
                ToolDefinition(
                    name=name,
                    description=description,
                    parameters=parameters,
                )
            )
        return tools

    def execute(self, tool_name: str, args: dict[str, Any]) -> list[Command]:
        """Execute a tool call, returning Commands (not platform HTTP).

        When a thread is bound, commands are also enqueued there so a hosted
        turn actually sends them — same as TypeScript ``tool.execute()``.
        ``send_dm`` still names a different ``Command.thread_id``; it lands on
        the handler thread's list, not a throwaway Thread.
        """
        thread_id = ThreadId(
            args.get("thread_id", "")
            or (str(self._thread.thread_id) if self._thread else "")
        )
        scratch = Thread(thread_id=thread_id)

        match tool_name:
            case "post_message":
                scratch.post(args["text"])
            case "edit_message":
                scratch.edit(args["message_id"], args["text"])
            case "add_reaction":
                scratch.react(args["message_id"], args["emoji"])
            case "start_typing":
                scratch.typing()
            case "send_dm":
                scratch.initiate(args["text"])
            case _:
                pass

        commands = scratch.commands
        if self._thread is not None:
            for command in commands:
                self._thread.enqueue(command)
        return commands
