"""Modal Sandbox-based remote workspace implementation."""

from __future__ import annotations

import contextlib
import os
import secrets
import time
from typing import Any, Literal

import httpx
import modal
from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.remote.base import RemoteWorkspace


logger = get_logger(__name__)

DEFAULT_AGENT_SERVER_PORT = 8000
MAX_SANDBOX_LIFETIME_SECONDS = 24 * 60 * 60


class ModalWorkspace(RemoteWorkspace):
    """Remote workspace hosted in a Modal Sandbox.

    The workspace provisions a Sandbox from an image containing the OpenHands
    agent-server, exposes the server through an encrypted Modal tunnel, and then
    uses the standard remote workspace API for commands, files, git operations,
    and conversations.

    Install the optional dependency before using this backend:

    ``pip install 'openhands-workspace[modal]'``
    """

    working_dir: str = Field(
        default="/workspace",
        description="Working directory inside the Modal Sandbox.",
    )
    host: str = Field(
        default="",
        description="Agent-server tunnel URL, populated during startup.",
    )
    server_image: str | None = Field(
        default=None,
        description=(
            "Container image containing the OpenHands agent-server. Required when "
            "creating a Sandbox and unused when attaching by sandbox_id."
        ),
    )
    app_name: str = Field(
        default="openhands-agent-server",
        description="Modal App used to own newly created Sandboxes.",
    )
    modal_environment: str | None = Field(
        default=None,
        description="Optional Modal environment used when looking up the App.",
    )
    sandbox_id: str | None = Field(
        default=None,
        description="Existing running Sandbox ID to attach to instead of creating one.",
    )
    sandbox_name: str | None = Field(
        default=None,
        description="Optional name for a newly created Sandbox.",
    )
    target_type: Literal["binary", "source"] = Field(
        default="binary",
        description="Agent-server installation type in the container image.",
    )
    agent_server_port: int = Field(
        default=DEFAULT_AGENT_SERVER_PORT,
        ge=1,
        le=65535,
        description="Container port used by the agent-server.",
    )
    timeout: int = Field(
        default=3600,
        ge=1,
        le=MAX_SANDBOX_LIFETIME_SECONDS,
        description="Maximum Sandbox lifetime in seconds.",
    )
    idle_timeout: int | None = Field(
        default=None,
        ge=1,
        description="Optional Modal Sandbox idle timeout in seconds.",
    )
    startup_timeout: float = Field(
        default=600.0,
        gt=0,
        description="Maximum seconds to wait for the agent-server health endpoint.",
    )
    cpu: float | tuple[float, float] | None = Field(
        default=None,
        description="Modal physical CPU request or request/limit pair.",
    )
    memory: int | tuple[int, int] | None = Field(
        default=None,
        description="Modal memory request or request/limit pair in MiB.",
    )
    cloud: str | None = Field(
        default=None,
        description="Optional cloud provider selection passed to Modal.",
    )
    region: str | list[str] | None = Field(
        default=None,
        description="Optional Modal region or ordered region preference.",
    )
    registry_secret_name: str | None = Field(
        default=None,
        description="Optional Modal Secret used to pull a private registry image.",
    )
    forward_env: list[str] = Field(
        default_factory=list,
        description="Host environment variable names forwarded to the Sandbox.",
    )
    sandbox_tags: dict[str, str] = Field(
        default_factory=dict,
        description="Tags attached to a newly created Modal Sandbox.",
    )
    inbound_cidr_allowlist: list[str] | None = Field(
        default=None,
        description="Optional CIDRs allowed to connect to the Sandbox tunnel.",
    )
    keep_alive: bool = Field(
        default=False,
        description="Detach without terminating the Sandbox during cleanup.",
    )
    expected_server_git_sha: str | None = Field(
        default=None,
        description="Expected agent-server build SHA or SHA prefix.",
    )
    verbose: bool = Field(
        default=False,
        description="Enable Modal image build and Sandbox creation output.",
    )

    _sandbox: modal.Sandbox | None = PrivateAttr(default=None)

    def model_post_init(self, context: Any) -> None:
        """Provision or attach to a Sandbox and initialize the remote client."""
        try:
            self._start_or_attach()
            super().model_post_init(context)
        except Exception:
            self.cleanup()
            raise

    def _start_or_attach(self) -> None:
        if self.sandbox_id is not None:
            if not self.api_key:
                raise ValueError(
                    "api_key is required when attaching to an existing Modal Sandbox"
                )
            logger.info("Attaching to Modal Sandbox %s", self.sandbox_id)
            self._sandbox = modal.Sandbox.from_id(self.sandbox_id)
        else:
            self._create_sandbox()

        assert self._sandbox is not None
        tunnel_timeout = max(1, min(int(self.startup_timeout), 300))
        tunnels = self._sandbox.tunnels(timeout=tunnel_timeout)
        tunnel = tunnels.get(self.agent_server_port)
        if tunnel is None:
            raise RuntimeError(
                "Modal did not expose the requested agent-server port "
                f"{self.agent_server_port}"
            )

        self.host = tunnel.url.rstrip("/")
        self.reset_client()
        self._wait_for_health()
        self._validate_server_version()
        logger.info("Modal workspace %s is ready at %s", self.sandbox_id, self.host)

    def _create_sandbox(self) -> None:
        if self.server_image is None:
            raise ValueError("server_image is required when creating a Modal Sandbox")

        app_kwargs: dict[str, Any] = {"create_if_missing": True}
        if self.modal_environment is not None:
            app_kwargs["environment_name"] = self.modal_environment
        app = modal.App.lookup(self.app_name, **app_kwargs)

        registry_secret = None
        if self.registry_secret_name is not None:
            registry_secret = modal.Secret.from_name(self.registry_secret_name)
        image = modal.Image.from_registry(
            self.server_image,
            secret=registry_secret,
            setup_dockerfile_commands=["ENTRYPOINT []"],
        )

        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)

        environment = {
            name: os.environ[name] for name in self.forward_env if name in os.environ
        }
        environment["OH_SESSION_API_KEYS_0"] = self.api_key

        create_kwargs: dict[str, Any] = {
            "app": app,
            "image": image,
            "env": environment,
            "timeout": self.timeout,
            "encrypted_ports": [self.agent_server_port],
            "tags": self.sandbox_tags,
            "verbose": self.verbose,
        }
        optional_kwargs = {
            "name": self.sandbox_name,
            "idle_timeout": self.idle_timeout,
            "cpu": self.cpu,
            "memory": self.memory,
            "cloud": self.cloud,
            "region": self.region,
            "inbound_cidr_allowlist": self.inbound_cidr_allowlist,
        }
        create_kwargs.update(
            {key: value for key, value in optional_kwargs.items() if value is not None}
        )

        logger.info("Creating Modal Sandbox from %s", self.server_image)
        output_context = (
            modal.enable_output() if self.verbose else contextlib.nullcontext()
        )
        with output_context:
            self._sandbox = modal.Sandbox.create(
                *self._agent_server_command(),
                **create_kwargs,
            )
        self.sandbox_id = self._sandbox.object_id

    def _agent_server_command(self) -> tuple[str, ...]:
        executable = (
            "/usr/local/bin/openhands-agent-server"
            if self.target_type == "binary"
            else "/agent-server/.venv/bin/python"
        )
        command = ["tini", "--", executable]
        if self.target_type == "source":
            command.extend(["-m", "openhands.agent_server"])
        command.extend(["--host", "0.0.0.0", "--port", str(self.agent_server_port)])
        return tuple(command)

    def _wait_for_health(self) -> None:
        assert self._sandbox is not None
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                response = self.client.get("/health", timeout=5.0)
                response.raise_for_status()
                return
            except (httpx.HTTPError, OSError) as error:
                last_error = error

            return_code = self._sandbox.poll()
            if return_code is not None:
                raise RuntimeError(
                    "Modal Sandbox stopped before the agent-server became healthy "
                    f"with exit code {return_code}"
                )
            time.sleep(1.0)

        raise RuntimeError(
            "Modal agent-server did not become healthy within "
            f"{self.startup_timeout} seconds: {last_error}"
        )

    def _validate_server_version(self) -> None:
        if self.expected_server_git_sha is None:
            return

        server_info = self.get_server_info()
        actual_sha = str(server_info.get("build_git_sha", "unknown"))
        if not actual_sha.startswith(self.expected_server_git_sha):
            raise RuntimeError(
                "Modal agent-server revision mismatch: expected "
                f"{self.expected_server_git_sha}, got {actual_sha}"
            )

    def cleanup(self) -> None:
        """Close clients and terminate or detach from the Modal Sandbox."""
        sandbox = self._sandbox
        self._sandbox = None
        self.reset_client()
        if sandbox is None:
            return

        try:
            if self.keep_alive:
                logger.info("Keeping Modal Sandbox %s alive", self.sandbox_id)
            else:
                logger.info("Terminating Modal Sandbox %s", self.sandbox_id)
                sandbox.terminate(wait=True)
        except Exception as error:
            logger.warning("Modal Sandbox cleanup failed: %s", error)
        finally:
            try:
                sandbox.detach()
            except Exception as error:
                logger.warning("Modal Sandbox detach failed: %s", error)

        if not self.keep_alive:
            self.sandbox_id = None
            self.api_key = None
            self.host = ""

    def __enter__(self) -> ModalWorkspace:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        super().__exit__(exc_type, exc_val, exc_tb)
        self.cleanup()
