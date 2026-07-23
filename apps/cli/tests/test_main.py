from argparse import Namespace

import httpx
import pytest
from caspian_cli import main


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(main, "ENV_PATH", path)
    monkeypatch.delenv("CASPIAN_API_KEY", raising=False)
    monkeypatch.delenv("COMM_API_KEY", raising=False)
    monkeypatch.delenv("CASPIAN_BASE_URL", raising=False)
    monkeypatch.delenv("COMM_BASE_URL", raising=False)
    return path


def test_write_env_preserves_unrelated_values_and_replaces_keys(env_path):
    env_path.write_text("KEEP=yes\nCASPIAN_API_KEY=old\n")

    main._write_env({"CASPIAN_API_KEY": "new", "CASPIAN_BASE_URL": "https://gateway.test"})

    assert env_path.read_text() == (
        "KEEP=yes\nCASPIAN_API_KEY=new\nCASPIAN_BASE_URL=https://gateway.test\n"
    )


def test_config_prefers_environment_over_dotenv(env_path, monkeypatch):
    env_path.write_text("CASPIAN_API_KEY=file-key\nCASPIAN_BASE_URL=https://file.test\n")
    monkeypatch.setenv("CASPIAN_API_KEY", "env-key")
    monkeypatch.setenv("CASPIAN_BASE_URL", "https://env.test")

    assert main._config() == ("env-key", "https://env.test")


def test_config_exits_when_api_key_is_missing(env_path):
    with pytest.raises(SystemExit, match="No CASPIAN_API_KEY found"):
        main._config()


def test_request_sends_auth_and_returns_json(env_path, monkeypatch):
    env_path.write_text("CASPIAN_API_KEY=test-key\nCASPIAN_BASE_URL=https://gateway.test\n")
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(method=method, url=url, **kwargs)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(main.httpx, "request", fake_request)

    assert main._request("POST", "/v1/example", json_body={"x": 1}) == {"ok": True}
    assert seen["method"] == "POST"
    assert seen["url"] == "https://gateway.test/v1/example"
    assert seen["headers"] == {"Authorization": "Bearer test-key"}
    assert seen["json"] == {"x": 1}


def test_request_exits_with_api_error_detail(env_path, monkeypatch):
    env_path.write_text("CASPIAN_API_KEY=test-key\n")
    monkeypatch.setattr(
        main.httpx,
        "request",
        lambda *args, **kwargs: httpx.Response(403, json={"detail": "denied"}),
    )

    with pytest.raises(SystemExit, match="Error 403: denied"):
        main._request("GET", "/v1/private")


def test_request_text_returns_zone_file_body(env_path, monkeypatch):
    env_path.write_text("CASPIAN_API_KEY=test-key\nCASPIAN_BASE_URL=https://gateway.test\n")
    request = httpx.Request("GET", "https://gateway.test/v1/domains/domain_1/zone-file")
    def fake_request(*args, **kwargs):
        return httpx.Response(
            200,
            text="@ IN SOA ns.example.test. hostmaster.example.test.",
            request=request,
        )

    monkeypatch.setattr(main.httpx, "request", fake_request)

    assert main._request_text("GET", "/v1/domains/domain_1/zone-file") == (
        "@ IN SOA ns.example.test. hostmaster.example.test."
    )


def test_request_text_maps_zone_file_errors(env_path, monkeypatch):
    env_path.write_text("CASPIAN_API_KEY=test-key\nCASPIAN_BASE_URL=https://gateway.test\n")
    request = httpx.Request("GET", "https://gateway.test/v1/domains/domain_1/zone-file")
    def fake_request(*args, **kwargs):
        return httpx.Response(404, json={"detail": "domain not found"}, request=request)

    monkeypatch.setattr(main.httpx, "request", fake_request)

    with pytest.raises(SystemExit, match="Error 404: domain not found"):
        main._request_text("GET", "/v1/domains/domain_1/zone-file")


@pytest.mark.parametrize(
    ("cents", "expected"),
    [(None, "-"), (0, "$0.00"), (123, "$1.23")],
)
def test_fmt_cents(cents, expected):
    assert main._fmt_cents(cents) == expected


def test_cmd_status_formats_connections(monkeypatch, capsys):
    monkeypatch.setattr(
        main,
        "_request",
        lambda *args, **kwargs: [
            {
                "id": "conn_1",
                "channel": "email",
                "status": "active",
                "address": "agent@example.test",
            }
        ],
    )

    main.cmd_status(Namespace())

    assert "conn_1  email  active" in capsys.readouterr().out


def test_cmd_init_writes_mocked_project_credentials(env_path, monkeypatch, capsys):
    request = httpx.Request("POST", "https://gateway.test/v1/projects/sandbox")
    response = httpx.Response(
        200,
        request=request,
        json={"project_id": "project_1", "api_key": "sandbox-key"},
    )
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setattr(main.httpx, "post", fake_post)

    main.cmd_init(Namespace(gateway="https://gateway.test/", name="demo", force=False))

    assert calls == [
        (
            ("https://gateway.test/v1/projects/sandbox",),
            {"json": {"name": "demo"}, "timeout": 30},
        )
    ]
    assert env_path.read_text() == (
        "CASPIAN_API_KEY=sandbox-key\nCASPIAN_BASE_URL=https://gateway.test\n"
    )
    assert "Project project_1 created." in capsys.readouterr().out
