"""Sailbox-based remote workspace implementation."""

from __future__ import annotations

import os
import secrets
import time
from typing import Any, Literal

import httpx
import sail
from pydantic import Field, PrivateAttr

from openhands.sdk.logger import get_logger
from openhands.sdk.workspace.remote.base import RemoteWorkspace


logger = get_logger(__name__)

DEFAULT_AGENT_SERVER_IMAGE = "ghcr.io/openhands/agent-server:latest-python"
DEFAULT_AGENT_SERVER_PORT = 8000


class SailWorkspace(RemoteWorkspace):
    """Remote workspace hosted in a Sailbox.

    Sail imports the OpenHands agent-server OCI image as the Sailbox root
    filesystem. Because Sail does not run an image's ``ENTRYPOINT`` or ``CMD``,
    this backend explicitly starts the agent-server and exposes it through a
    Sail HTTP listener.

    Install the optional dependency before using this backend:

    ``pip install 'openhands-workspace[sail]'``
    """

    working_dir: str = Field(
        default="/workspace",
        description="Working directory inside the Sailbox.",
    )
    host: str = Field(
        default="",
        description="Agent-server listener URL, populated during startup.",
    )
    server_image: str = Field(
        default=DEFAULT_AGENT_SERVER_IMAGE,
        description="Public OCI image containing the OpenHands agent-server.",
    )
    app_name: str = Field(
        default="openhands-agent-server",
        description="Sail App used to own newly created Sailboxes.",
    )
    sailbox_id: str | None = Field(
        default=None,
        description="Existing Sailbox ID to attach to instead of creating one.",
    )
    sailbox_name: str | None = Field(
        default=None,
        description="Name for a newly created Sailbox; generated when omitted.",
    )
    target_type: Literal["binary", "source"] = Field(
        default="binary",
        description="Agent-server installation type in the imported image.",
    )
    agent_server_port: int = Field(
        default=DEFAULT_AGENT_SERVER_PORT,
        ge=1,
        le=65535,
        description="Guest port used by the agent-server.",
    )
    image_architecture: Literal["amd64", "arm64"] | None = Field(
        default=None,
        description="Optional architecture required from the registry image.",
    )
    image_build_timeout: int = Field(
        default=1800,
        gt=0,
        description="Maximum seconds to import and build the registry image.",
    )
    force_image_build: bool = Field(
        default=False,
        description="Refresh a mutable registry tag before creating the Sailbox.",
    )
    create_timeout: int = Field(
        default=600,
        ge=0,
        description=(
            "Maximum seconds for each Sailbox creation attempt; 0 is unbounded."
        ),
    )
    startup_timeout: float = Field(
        default=600.0,
        gt=0,
        description="Maximum seconds to wait for the agent-server health endpoint.",
    )
    size: Literal["s", "m", "l"] | None = Field(
        default=None,
        description="Optional Sailbox resource ceiling.",
    )
    memory_limit_gib: int | None = Field(
        default=None,
        gt=0,
        description="Optional Sailbox memory ceiling in GiB.",
    )
    disk_limit_gib: int | None = Field(
        default=None,
        gt=0,
        description="Optional Sailbox disk ceiling in GiB.",
    )
    private: bool = Field(
        default=False,
        description="Restrict Sailbox operations to the creating Sail user.",
    )
    inbound_allowlist: list[str] | None = Field(
        default=None,
        description="Optional source addresses, ranges, or Sail Apps for HTTP ingress.",
    )
    forward_env: list[str] = Field(
        default_factory=list,
        description="Host environment variable names forwarded to the agent-server.",
    )
    workspace_owner: str | None = Field(
        default="10001:10001",
        description=(
            "Owner assigned to working_dir. The public OpenHands image uses "
            "UID/GID 10001; set to None to preserve existing ownership."
        ),
    )
    keep_alive: bool = Field(
        default=False,
        description="Leave the Sailbox running during cleanup for later attachment.",
    )
    expected_server_git_sha: str | None = Field(
        default=None,
        description="Expected agent-server build SHA or SHA prefix.",
    )

    _sailbox: sail.Sailbox | None = PrivateAttr(default=None)
    _server_process: Any = PrivateAttr(default=None)

    def model_post_init(self, context: Any) -> None:
        """Provision or attach to a Sailbox and initialize the remote client."""
        try:
            self._start_or_attach()
            super().model_post_init(context)
        except Exception:
            self.cleanup()
            raise

    def _start_or_attach(self) -> None:
        if self.sailbox_id is not None:
            if not self.api_key:
                raise ValueError(
                    "api_key is required when attaching to an existing Sailbox"
                )
            logger.info("Attaching to Sailbox %s", self.sailbox_id)
            self._sailbox = sail.Sailbox.get(self.sailbox_id)
        else:
            self._create_sailbox()

        assert self._sailbox is not None
        listener = self._sailbox.wait_for_listener(
            self.agent_server_port,
            timeout=self.startup_timeout,
        )
        endpoint = listener.endpoint
        endpoint_url = getattr(endpoint, "url", None)
        if endpoint_url is None:
            raise RuntimeError(
                "Sail did not expose an HTTP listener for agent-server port "
                f"{self.agent_server_port}"
            )

        self.host = str(endpoint_url).rstrip("/")
        self.reset_client()
        self._wait_for_health()
        self._validate_server_version()
        logger.info("Sail workspace %s is ready at %s", self.sailbox_id, self.host)

    def _create_sailbox(self) -> None:
        app = sail.App.find(name=self.app_name, mint_if_missing=True)
        image = sail.Image.from_registry(
            self.server_image,
            architecture=self.image_architecture,
        )
        if self.force_image_build:
            image = image.build(
                timeout=self.image_build_timeout,
                force_build=True,
            )

        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)
        if self.sailbox_name is None:
            self.sailbox_name = f"openhands-agent-server-{secrets.token_hex(4)}"

        ingress_port = sail.IngressPort(
            self.agent_server_port,
            "http",
            allowlist=self.inbound_allowlist,
        )
        create_kwargs: dict[str, Any] = {
            "app": app,
            "image": image,
            "name": self.sailbox_name,
            "image_build_timeout": self.image_build_timeout,
            "timeout": self.create_timeout,
            "ingress_ports": [ingress_port],
            "private": self.private,
        }
        optional_kwargs = {
            "size": self.size,
            "memory_limit_gib": self.memory_limit_gib,
            "disk_limit_gib": self.disk_limit_gib,
        }
        create_kwargs.update(
            {key: value for key, value in optional_kwargs.items() if value is not None}
        )

        logger.info("Creating Sailbox from %s", self.server_image)
        self._sailbox = sail.Sailbox.create(**create_kwargs)
        self.sailbox_id = self._sailbox.sailbox_id
        self._prepare_working_directory()
        self._start_agent_server()

    def _prepare_working_directory(self) -> None:
        assert self._sailbox is not None
        self._sailbox.run(
            ["mkdir", "-p", "--", self.working_dir],
            user="0:0",
            check=True,
        )
        if self.workspace_owner is not None:
            self._sailbox.run(
                ["chown", self.workspace_owner, self.working_dir],
                user="0:0",
                check=True,
            )

    def _start_agent_server(self) -> None:
        assert self._sailbox is not None
        environment = {
            name: os.environ[name] for name in self.forward_env if name in os.environ
        }
        assert self.api_key is not None
        environment["OH_SESSION_API_KEYS_0"] = self.api_key
        self._server_process = self._sailbox.exec(
            self._agent_server_command(),
            env=environment,
        )

    def _agent_server_command(self) -> list[str]:
        if self.target_type == "binary":
            command = ["/usr/local/bin/openhands-agent-server"]
        else:
            command = [
                "/agent-server/.venv/bin/python",
                "-m",
                "openhands.agent_server",
            ]
        command.extend(["--host", "0.0.0.0", "--port", str(self.agent_server_port)])
        return command

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                response = self.client.get("/health", timeout=5.0)
                response.raise_for_status()
                return
            except (httpx.HTTPError, OSError) as error:
                last_error = error

            if self._server_process is not None:
                return_code = self._server_process.poll()
                if return_code is not None:
                    try:
                        stderr = "".join(self._server_process.stderr).strip()
                    except Exception:
                        stderr = ""
                    details = f": {stderr}" if stderr else ""
                    raise RuntimeError(
                        "Sailbox agent-server stopped before becoming healthy "
                        f"with exit code {return_code}{details}"
                    )
            time.sleep(1.0)

        raise RuntimeError(
            "Sailbox agent-server did not become healthy within "
            f"{self.startup_timeout} seconds: {last_error}"
        )

    def _validate_server_version(self) -> None:
        if self.expected_server_git_sha is None:
            return

        server_info = self.get_server_info()
        actual_sha = str(server_info.get("build_git_sha", "unknown"))
        if not actual_sha.startswith(self.expected_server_git_sha):
            raise RuntimeError(
                "Sailbox agent-server revision mismatch: expected "
                f"{self.expected_server_git_sha}, got {actual_sha}"
            )

    def pause(self) -> None:
        """Checkpoint and pause the Sailbox in memory."""
        if self._sailbox is None:
            raise RuntimeError("Cannot pause: Sailbox is not running")
        self.reset_client()
        self._sailbox.pause()

    def resume(self) -> None:
        """Resume the Sailbox and wait for the agent-server to become healthy."""
        if self._sailbox is None:
            raise RuntimeError("Cannot resume: Sailbox is not running")
        self._sailbox = self._sailbox.resume()
        listener = self._sailbox.wait_for_listener(
            self.agent_server_port,
            timeout=self.startup_timeout,
        )
        endpoint = listener.endpoint
        endpoint_url = getattr(endpoint, "url", None)
        if endpoint_url is None:
            raise RuntimeError("Sailbox agent-server HTTP listener is unavailable")
        self.host = str(endpoint_url).rstrip("/")
        self.reset_client()
        self._wait_for_health()

    def cleanup(self) -> None:
        """Close local clients and terminate or retain the Sailbox."""
        sailbox = self._sailbox
        process = self._server_process
        self._sailbox = None
        self._server_process = None
        self.reset_client()

        if sailbox is not None:
            try:
                if self.keep_alive:
                    logger.info("Keeping Sailbox %s alive", self.sailbox_id)
                else:
                    logger.info("Terminating Sailbox %s", self.sailbox_id)
                    sailbox.terminate()
            except Exception as error:
                logger.warning("Sailbox cleanup failed: %s", error)

        if process is not None:
            try:
                process.close()
            except Exception as error:
                logger.warning("Sailbox process handle cleanup failed: %s", error)

        if not self.keep_alive:
            self.sailbox_id = None
            self.api_key = None
            self.host = ""

    def __enter__(self) -> SailWorkspace:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        super().__exit__(exc_type, exc_val, exc_tb)
        self.cleanup()
