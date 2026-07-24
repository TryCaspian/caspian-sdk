import json
import hmac
import hashlib
import pytest
from caspian_sdk import CommClient, WebhookVerificationError
import httpx

API_KEY = "comm_test_key"

def _client() -> CommClient:
    return CommClient(api_key=API_KEY, base_url="http://gw.test", http=httpx.Client(transport=httpx.MockTransport(lambda x: httpx.Response(404))))

def test_handle_webhook_valid_signature():
    client = _client()
    seen = []
    @client.on_message
    def handler(msg):
        seen.append(msg.id)

    payload = json.dumps({
        "type": "message.received",
        "seq": 1,
        "id": "event_1",
        "data": {
            "customer_id": "cus_1",
            "agent_id": "agt_1",
            "message": {
                "id": "msg_1",
                "conversation_id": "conv_1",
                "connection_id": "conn_1"
            }
        }
    }).encode("utf-8")
    secret = "my_secret"
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    # Valid payload should dispatch and add msg_1 to seen
    client.handle_webhook(payload, signature, secret)
    assert seen == ["msg_1"]

    # Deduplication test - same event id shouldn't fire again
    client.handle_webhook(payload, signature, secret)
    assert seen == ["msg_1"]  # Still just 1

def test_handle_webhook_invalid_signature():
    client = _client()
    payload = b'{"id":"event_2"}'
    with pytest.raises(WebhookVerificationError):
        client.handle_webhook(payload, "bad_signature", "secret")
