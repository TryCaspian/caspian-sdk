from caspian_cli.catalog import get_catalog, load_catalog, search_catalog
from caspian_cli.desugar import parse_argv
from caspian_cli.intent import CatalogGet, CatalogSearch


def test_catalog_lists_post_and_telegram_send_photo():
    ids = {e["id"] for e in load_catalog()}
    assert "post" in ids
    assert "telegram.send-photo" in ids
    assert "slack.post" not in ids


def test_catalog_search_photo():
    hits = search_catalog("send a photo")
    assert any(e["id"] == "telegram.send-photo" for e in hits)


def test_catalog_get():
    entry = get_catalog("telegram.send-photo")
    assert entry["command_tag"] == "SendMedia"


def test_argv_catalog_does_not_invoke():
    assert parse_argv(["catalog", "search", "send a photo"]) == CatalogSearch(
        query="send a photo"
    )
    assert parse_argv(["catalog", "get", "telegram.send-photo"]) == CatalogGet(
        id="telegram.send-photo"
    )
