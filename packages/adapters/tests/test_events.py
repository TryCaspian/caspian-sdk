"""Gateway-facing adapter event classification."""

import pytest
from caspian_adapters import (
    COMMAND_RECEIVED,
    MESSAGE_RECEIVED,
    REACTION_RECEIVED,
    InboundCommand,
    InboundMessage,
    InboundReaction,
    event_payload,
    event_type,
)


def test_message_maps_to_message_received():
    event = InboundMessage(
        external_event_id="evt_1",
        provider_inbox_id="inbox_1",
        provider_message_id="msg_1",
        provider_thread_id="thread_1",
        text="hello",
    )

    assert event_type(event) == MESSAGE_RECEIVED
    assert event_payload(event)["text"] == "hello"


def test_reaction_maps_to_reaction_received():
    event = InboundReaction(
        external_event_id="evt_2",
        provider_inbox_id="inbox_1",
        provider_message_id="msg_1",
        provider_thread_id="thread_1",
        source_provider_message_id="msg_1",
        emoji="thumbsup",
        action="added",
        sender_address="U123",
    )

    assert event_type(event) == REACTION_RECEIVED
    assert event_payload(event)["emoji"] == "thumbsup"
    assert event_payload(event)["action"] == "added"
    assert event_payload(event)["source_provider_message_id"] == "msg_1"


def test_command_maps_to_command_received():
    event = InboundCommand(
        external_event_id="evt_3",
        provider_inbox_id="inbox_1",
        provider_message_id="cmd_1",
        provider_thread_id="thread_1",
        command="triage",
        args="urgent inbox",
        text="/triage urgent inbox",
        sender_address="U123",
    )

    assert event_type(event) == COMMAND_RECEIVED
    assert event_payload(event)["command"] == "triage"
    assert event_payload(event)["args"] == "urgent inbox"


def test_unknown_event_type_is_rejected():
    with pytest.raises(TypeError, match="unknown inbound event type"):
        event_type(object())
