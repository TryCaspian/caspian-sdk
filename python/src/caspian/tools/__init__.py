"""Tools — agent tool view derived from Command types.

Tools are an interpreter of the same Commands; a tool that bypasses the
Command path and calls the platform directly is a bug.
"""

from __future__ import annotations

from typing import Any

from caspian.core.commands import Command
from caspian.core.types import ThreadId
from caspian.facade.thread import Thread


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
        """All tool definitions for the current preset."""
        tools = [
            ToolDefinition(
                name="post_message",
                description="Send a message to the current thread",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Message text (markdown)"},
                        "thread_id": {"type": "string", "description": "Target thread id"},
                    },
                    "required": ["text"],
                },
            ),
            ToolDefinition(
                name="edit_message",
                description="Edit an existing message",
                parameters={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "text": {"type": "string"},
                        "thread_id": {"type": "string"},
                    },
                    "required": ["message_id", "text"],
                },
            ),
            ToolDefinition(
                name="add_reaction",
                description="React to a message with an emoji",
                parameters={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "emoji": {"type": "string"},
                        "thread_id": {"type": "string"},
                    },
                    "required": ["message_id", "emoji"],
                },
            ),
            ToolDefinition(
                name="start_typing",
                description="Show typing indicator",
                parameters={
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                    },
                },
            ),
        ]

        if self._preset == "outbound":
            tools.append(
                ToolDefinition(
                    name="send_dm",
                    description="Send a direct message to a user",
                    parameters={
                        "type": "object",
                        "properties": {
                            "thread_id": {"type": "string", "description": "User's thread id"},
                            "text": {"type": "string"},
                        },
                        "required": ["thread_id", "text"],
                    },
                )
            )

        return tools

    def execute(self, tool_name: str, args: dict[str, Any]) -> list[Command]:
        """Execute a tool call, returning Commands (not platform HTTP).

        This ensures tools go through the same Command path as handlers.
        """
        thread_id = ThreadId(args.get("thread_id", "") or (
            str(self._thread.thread_id) if self._thread else ""
        ))
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
                thread.post(args["text"])
            case _:
                pass

        return thread.commands
