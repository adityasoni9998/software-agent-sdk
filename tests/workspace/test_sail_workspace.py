"""Tests for the Sailbox workspace backend."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace import SailWorkspace


_wait_for_health = SailWorkspace._wait_for_health


@pytest.fixture
def sail_backend():
    app = MagicMock()
    image = MagicMock()
    process = MagicMock()
    process.poll.return_value = None
    process.stderr = ""
    sailbox = MagicMock()
    sailbox.sailbox_id = "sb-test"
    sailbox.exec.return_value = process
    sailbox.resume.return_value = sailbox
    sailbox.wait_for_listener.return_value = SimpleNamespace(
        endpoint=SimpleNamespace(url="https://sailbox.example.com")
    )

    with (
        patch(
            "openhands.workspace.sail.workspace.sail.App.find",
            return_value=app,
        ) as app_find,
        patch(
            "openhands.workspace.sail.workspace.sail.Image.from_registry",
            return_value=image,
        ) as image_from_registry,
        patch(
            "openhands.workspace.sail.workspace.sail.Sailbox.create",
            return_value=sailbox,
        ) as sailbox_create,
        patch(
            "openhands.workspace.sail.workspace.sail.Sailbox.get",
            return_value=sailbox,
        ) as sailbox_get,
        patch.object(SailWorkspace, "_wait_for_health") as wait_for_health,
    ):
        yield SimpleNamespace(
            app=app,
            app_find=app_find,
            image=image,
            image_from_registry=image_from_registry,
            process=process,
            sailbox=sailbox,
            sailbox_create=sailbox_create,
            sailbox_get=sailbox_get,
            wait_for_health=wait_for_health,
        )


def test_sail_workspace_is_remote_workspace():
    assert issubclass(SailWorkspace, RemoteWorkspace)


def test_sail_workspace_creates_secured_sailbox(sail_backend):
    with patch.dict(os.environ, {"FORWARDED_TOKEN": "token-value"}):
        workspace = SailWorkspace(
            server_image="ghcr.io/openhands/agent-server:latest-python",
            api_key="session-key",
            target_type="source",
            app_name="test-app",
            sailbox_name="test-sailbox",
            size="l",
            memory_limit_gib=32,
            disk_limit_gib=128,
            private=True,
            inbound_allowlist=["runner-app"],
            forward_env=["FORWARDED_TOKEN", "MISSING_TOKEN"],
        )

    sail_backend.app_find.assert_called_once_with(name="test-app", mint_if_missing=True)
    sail_backend.image_from_registry.assert_called_once_with(
        "ghcr.io/openhands/agent-server:latest-python",
        architecture=None,
    )
    create_call = sail_backend.sailbox_create.call_args.kwargs
    assert create_call["app"] is sail_backend.app
    assert create_call["image"] is sail_backend.image
    assert create_call["name"] == "test-sailbox"
    assert create_call["size"] == "l"
    assert create_call["memory_limit_gib"] == 32
    assert create_call["disk_limit_gib"] == 128
    assert create_call["private"] is True
    ingress_port = create_call["ingress_ports"][0]
    assert ingress_port.guest_port == 8000
    assert ingress_port.protocol == "http"
    assert ingress_port.allowlist == ["runner-app"]

    assert sail_backend.sailbox.run.call_args_list[0].args[0] == [
        "mkdir",
        "-p",
        "--",
        "/workspace",
    ]
    assert sail_backend.sailbox.run.call_args_list[1].args[0] == [
        "chown",
        "10001:10001",
        "/workspace",
    ]
    exec_call = sail_backend.sailbox.exec.call_args
    assert exec_call.args[0] == [
        "/agent-server/.venv/bin/python",
        "-m",
        "openhands.agent_server",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert exec_call.kwargs["env"] == {
        "FORWARDED_TOKEN": "token-value",
        "OH_SESSION_API_KEYS_0": "session-key",
    }
    assert workspace.sailbox_id == "sb-test"
    assert workspace.host == "https://sailbox.example.com"
    sail_backend.wait_for_health.assert_called_once_with()

    workspace.cleanup()
    sail_backend.sailbox.terminate.assert_called_once_with()
    sail_backend.process.close.assert_called_once_with()


def test_sail_workspace_generates_name_and_session_key(sail_backend):
    workspace = SailWorkspace()

    assert workspace.api_key
    assert workspace.sailbox_name
    assert workspace.sailbox_name.startswith("openhands-agent-server-")
    environment = sail_backend.sailbox.exec.call_args.kwargs["env"]
    assert environment["OH_SESSION_API_KEYS_0"] == workspace.api_key
    assert sail_backend.sailbox.exec.call_args.args[0][0] == (
        "/usr/local/bin/openhands-agent-server"
    )

    workspace.cleanup()


def test_sail_workspace_can_force_registry_refresh(sail_backend):
    built_image = MagicMock()
    sail_backend.image.build.return_value = built_image

    workspace = SailWorkspace(force_image_build=True, image_build_timeout=900)

    sail_backend.image.build.assert_called_once_with(
        timeout=900,
        force_build=True,
    )
    assert sail_backend.sailbox_create.call_args.kwargs["image"] is built_image
    workspace.cleanup()


def test_sail_workspace_requires_key_when_attaching(sail_backend):
    with pytest.raises(ValueError, match="api_key is required"):
        SailWorkspace(sailbox_id="sb-existing")

    sail_backend.sailbox_get.assert_not_called()


def test_sail_workspace_attaches_to_running_sailbox(sail_backend):
    workspace = SailWorkspace(
        sailbox_id="sb-existing",
        api_key="existing-key",
    )

    sail_backend.sailbox_get.assert_called_once_with("sb-existing")
    sail_backend.sailbox_create.assert_not_called()
    assert workspace.host == "https://sailbox.example.com"
    workspace.cleanup()


def test_sail_workspace_keep_alive_retains_attachment_values(sail_backend):
    workspace = SailWorkspace(keep_alive=True)

    workspace.cleanup()

    sail_backend.sailbox.terminate.assert_not_called()
    sail_backend.process.close.assert_called_once_with()
    assert workspace.sailbox_id == "sb-test"
    assert workspace.api_key is not None
    assert workspace.host == "https://sailbox.example.com"


def test_sail_workspace_cleans_up_when_listener_is_missing(sail_backend):
    sail_backend.sailbox.wait_for_listener.return_value = SimpleNamespace(endpoint=None)

    with pytest.raises(RuntimeError, match="did not expose"):
        SailWorkspace()

    sail_backend.sailbox.terminate.assert_called_once_with()
    sail_backend.process.close.assert_called_once_with()


def test_sail_workspace_validates_server_revision(sail_backend):
    workspace = SailWorkspace()
    workspace.expected_server_git_sha = "abc1234"

    with patch.object(
        SailWorkspace,
        "get_server_info",
        return_value={"build_git_sha": "def5678"},
    ):
        with pytest.raises(RuntimeError, match="revision mismatch"):
            workspace._validate_server_version()

    workspace.cleanup()


def test_sail_workspace_health_check_detects_stopped_server(sail_backend):
    workspace = SailWorkspace()
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("connection failed")
    workspace._client = client
    sail_backend.process.poll.return_value = 137
    sail_backend.process.stderr = "killed"

    with pytest.raises(RuntimeError, match="exit code 137: killed"):
        _wait_for_health(workspace)

    workspace.cleanup()


def test_sail_workspace_pause_and_resume(sail_backend):
    workspace = SailWorkspace()

    workspace.pause()
    sail_backend.sailbox.pause.assert_called_once_with()

    workspace.resume()
    sail_backend.sailbox.resume.assert_called_once_with()
    assert workspace.host == "https://sailbox.example.com"
    assert sail_backend.wait_for_health.call_count == 2

    workspace.cleanup()
