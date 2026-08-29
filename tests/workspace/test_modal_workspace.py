"""Tests for the Modal Sandbox workspace backend."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace import ModalWorkspace


_wait_for_health = ModalWorkspace._wait_for_health


@pytest.fixture
def modal_backend():
    sandbox = MagicMock()
    sandbox.object_id = "sb-test"
    sandbox.poll.return_value = None
    sandbox.tunnels.return_value = {
        8000: SimpleNamespace(url="https://sandbox.modal.host")
    }

    with (
        patch("openhands.workspace.modal.workspace.modal.App.lookup") as app_lookup,
        patch(
            "openhands.workspace.modal.workspace.modal.Image.from_registry"
        ) as image_from_registry,
        patch(
            "openhands.workspace.modal.workspace.modal.Sandbox.create",
            return_value=sandbox,
        ) as sandbox_create,
        patch(
            "openhands.workspace.modal.workspace.modal.Sandbox.from_id",
            return_value=sandbox,
        ) as sandbox_from_id,
        patch.object(ModalWorkspace, "_wait_for_health") as wait_for_health,
    ):
        app = MagicMock()
        image = MagicMock()
        app_lookup.return_value = app
        image_from_registry.return_value = image
        yield SimpleNamespace(
            app=app,
            app_lookup=app_lookup,
            image=image,
            image_from_registry=image_from_registry,
            sandbox=sandbox,
            sandbox_create=sandbox_create,
            sandbox_from_id=sandbox_from_id,
            wait_for_health=wait_for_health,
        )


def test_modal_workspace_is_remote_workspace():
    assert issubclass(ModalWorkspace, RemoteWorkspace)


def test_modal_workspace_creates_secured_sandbox(modal_backend):
    with patch.dict(os.environ, {"FORWARDED_TOKEN": "token-value"}):
        workspace = ModalWorkspace(
            server_image="ghcr.io/openhands/agent-server:latest-python",
            api_key="session-key",
            target_type="source",
            app_name="test-app",
            sandbox_name="test-sandbox",
            cpu=(1.0, 4.0),
            memory=(2048, 8192),
            forward_env=["FORWARDED_TOKEN", "MISSING_TOKEN"],
            sandbox_tags={"run": "test"},
        )

    modal_backend.app_lookup.assert_called_once_with("test-app", create_if_missing=True)
    modal_backend.image_from_registry.assert_called_once_with(
        "ghcr.io/openhands/agent-server:latest-python",
        secret=None,
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )
    call = modal_backend.sandbox_create.call_args
    assert call.args == (
        "tini",
        "--",
        "/agent-server/.venv/bin/python",
        "-m",
        "openhands.agent_server",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    )
    assert call.kwargs["app"] is modal_backend.app
    assert call.kwargs["image"] is modal_backend.image
    assert call.kwargs["encrypted_ports"] == [8000]
    assert call.kwargs["env"] == {
        "FORWARDED_TOKEN": "token-value",
        "OH_SESSION_API_KEYS_0": "session-key",
    }
    assert call.kwargs["name"] == "test-sandbox"
    assert call.kwargs["cpu"] == (1.0, 4.0)
    assert call.kwargs["memory"] == (2048, 8192)
    assert call.kwargs["tags"] == {"run": "test"}
    assert workspace.sandbox_id == "sb-test"
    assert workspace.host == "https://sandbox.modal.host"
    modal_backend.wait_for_health.assert_called_once_with()

    workspace.cleanup()
    modal_backend.sandbox.terminate.assert_called_once_with(wait=True)
    modal_backend.sandbox.detach.assert_called_once_with()


def test_modal_workspace_generates_session_key(modal_backend):
    workspace = ModalWorkspace(server_image="example/image:tag")

    environment = modal_backend.sandbox_create.call_args.kwargs["env"]
    command = modal_backend.sandbox_create.call_args.args
    assert workspace.api_key
    assert environment["OH_SESSION_API_KEYS_0"] == workspace.api_key
    assert command[:3] == ("tini", "--", "/usr/local/bin/openhands-agent-server")
    assert "openhands.agent_server" not in command

    workspace.cleanup()


def test_modal_workspace_uses_registry_secret(modal_backend):
    registry_secret = MagicMock()
    with patch(
        "openhands.workspace.modal.workspace.modal.Secret.from_name",
        return_value=registry_secret,
    ) as secret_from_name:
        workspace = ModalWorkspace(
            server_image="private.example.com/image:tag",
            registry_secret_name="registry-credentials",
        )

    secret_from_name.assert_called_once_with("registry-credentials")
    modal_backend.image_from_registry.assert_called_once_with(
        "private.example.com/image:tag",
        secret=registry_secret,
        setup_dockerfile_commands=["ENTRYPOINT []"],
    )
    workspace.cleanup()


def test_modal_workspace_requires_key_when_attaching(modal_backend):
    with pytest.raises(ValueError, match="api_key is required"):
        ModalWorkspace(
            sandbox_id="sb-existing",
        )

    modal_backend.sandbox_from_id.assert_not_called()


def test_modal_workspace_attaches_to_running_sandbox(modal_backend):
    workspace = ModalWorkspace(
        sandbox_id="sb-existing",
        api_key="existing-key",
    )

    modal_backend.sandbox_from_id.assert_called_once_with("sb-existing")
    modal_backend.sandbox_create.assert_not_called()
    workspace.cleanup()


def test_modal_workspace_keep_alive_only_detaches(modal_backend):
    workspace = ModalWorkspace(
        server_image="example/image:tag",
        keep_alive=True,
    )

    workspace.cleanup()

    modal_backend.sandbox.terminate.assert_not_called()
    modal_backend.sandbox.detach.assert_called_once_with()
    assert workspace.sandbox_id == "sb-test"
    assert workspace.api_key is not None


def test_modal_workspace_cleans_up_when_tunnel_is_missing(modal_backend):
    modal_backend.sandbox.tunnels.return_value = {}

    with pytest.raises(RuntimeError, match="did not expose"):
        ModalWorkspace(server_image="example/image:tag")

    modal_backend.sandbox.terminate.assert_called_once_with(wait=True)
    modal_backend.sandbox.detach.assert_called_once_with()


def test_modal_workspace_validates_server_revision(modal_backend):
    workspace = ModalWorkspace(
        server_image="example/image:tag",
    )
    workspace.expected_server_git_sha = "abc1234"

    with patch.object(
        ModalWorkspace,
        "get_server_info",
        return_value={"build_git_sha": "def5678"},
    ):
        with pytest.raises(RuntimeError, match="revision mismatch"):
            workspace._validate_server_version()

    workspace.cleanup()


def test_modal_workspace_health_check_detects_stopped_sandbox(modal_backend):
    workspace = ModalWorkspace(server_image="example/image:tag")
    client = MagicMock()
    client.get.side_effect = httpx.ConnectError("connection failed")
    workspace._client = client
    modal_backend.sandbox.poll.return_value = 137

    with pytest.raises(RuntimeError, match="exit code 137"):
        _wait_for_health(workspace)

    workspace.cleanup()
