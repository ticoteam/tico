from unittest.mock import patch

from clients.tico import APIError
from connectors.mail.scheduling import bd_confirmed


def test_cloud_confirmation_uses_authenticated_message_and_never_local_sqlite():
    with patch.dict("os.environ", {"HUB_API_URL": "https://hub.acme.example", "HUB_TOKEN": "test-attempt"}), \
         patch("clients.tico.Client.get") as read, patch("sqlite3.connect", side_effect=AssertionError("must not read local hub.db")):
        read.return_value = {"from_actor": "bot:business-development", "body": "Scheduling approval: person@example.com\nThread: thread-id"}
        assert bd_confirmed("message-id", "person@example.com", "thread-id")
        assert not bd_confirmed("message-id", "person@example.com", "other-thread")
        read.side_effect = APIError("unavailable", "Cannot confirm", retryable=True)
        assert not bd_confirmed("message-id", "person@example.com", "thread-id")
