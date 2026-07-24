import httpx
from caspian_sdk import CommClient

API_KEY = "comm_test_key"


def _client(handler) -> CommClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    return CommClient(api_key=API_KEY, base_url="http://gw.test", http=http)


def _message(client: CommClient) -> dict:
    return {
        "id": "msg_1",
        "conversation_id": "conv_1",
        "connection_id": "conn_1",
        "customer_id": "cus_1",
        "agent_id": "agt_1",
        "channel": "slack",
        "sender": None,
        "subject": None,
        "text": "hello",
        "html": None,
        "media": [],
    }


def test_streaming_post_edit():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.read().decode("utf-8")))
        if request.method == "GET" and request.url.path == "/v1/connections/conn_1":
            return httpx.Response(200, json={"capabilities": ["edit_outbound"]})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "msg_reply_1"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "msg_reply_1"})
        return httpx.Response(404)

    client = _client(handler)
    try:
        msg = client._build_message({"message": _message(client)})
        # Use a short edit_interval to force edits
        with msg.stream(edit_interval=0.01) as s:
            s.append("chunk1")
            import time

            time.sleep(0.02)
            s.append(" chunk2")
    finally:
        client.close()

    assert len(seen) >= 3
    assert seen[0][0] == "GET"
    assert seen[0][1] == "/v1/connections/conn_1"
    assert seen[1][0] == "POST"
    assert seen[1][1] == "/v1/messages/msg_1/reply"
    assert "chunk1" in seen[1][2]
    # The final edit
    assert seen[-1][0] == "PATCH"
    assert seen[-1][1] == "/v1/messages/msg_reply_1"
    assert "chunk1 chunk2" in seen[-1][2]


def test_streaming_final_only():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.read().decode("utf-8")))
        if request.method == "GET" and request.url.path == "/v1/connections/conn_1":
            return httpx.Response(200, json={"capabilities": []})  # No edit_outbound
        if request.method == "POST":
            return httpx.Response(200, json={"id": "msg_reply_1"})
        return httpx.Response(404)

    client = _client(handler)
    try:
        msg = client._build_message({"message": _message(client)})
        with msg.stream(edit_interval=0.01) as s:
            s.append("chunk1")
            import time

            time.sleep(0.02)
            s.append(" chunk2")
    finally:
        client.close()

    assert len(seen) == 2
    assert seen[0][0] == "GET"
    assert seen[0][1] == "/v1/connections/conn_1"
    assert seen[1][0] == "POST"
    assert seen[1][1] == "/v1/messages/msg_1/reply"
    assert "chunk1 chunk2" in seen[1][2]
