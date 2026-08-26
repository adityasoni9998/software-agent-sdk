"""Run an LLM-backed CodeScout localization smoke test.

The test starts a remote custom source-minimal agent-server image, registers
``LocalizationFinishTool`` dynamically, and asks the model to localize a tiny
synthetic repository created in the workspace.
"""

import os
import platform
from collections.abc import Sequence
from typing import Any

import custom_tools.localization_finish  # noqa: F401
from custom_tools.localization_finish import LocalizationFinishAction
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, RemoteConversation, Tool, get_logger
from openhands.sdk.event import ActionEvent
from openhands.sdk.workspace import PlatformType, RemoteWorkspace
from openhands.tools.terminal import TerminalTool
from openhands.workspace import DockerWorkspace


logger = get_logger(__name__)

DEFAULT_IMAGE_TAG = (
    "docker.io/adityasoni8/codescout-agent-server-sail-workspace:"
    "codescout-sail-source-minimal"
)


def detect_platform() -> PlatformType:
    """Detect the correct Docker platform string."""
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


def extract_locations(events: Sequence[Any]) -> list[dict[str, str | None]]:
    """Extract the locations submitted through LocalizationFinishTool."""
    for event in reversed(events):
        if isinstance(event, ActionEvent) and isinstance(
            event.action, LocalizationFinishAction
        ):
            return [
                {
                    "file": location.file,
                    "class_name": location.class_name,
                    "function_name": location.function_name,
                }
                for location in event.action.locations
            ]
    return []


def create_workspace(image_tag: str, host_port: int) -> RemoteWorkspace:
    """Create the selected remote workspace for the CodeScout image."""
    backend = os.getenv("CODESCOUT_WORKSPACE", "docker")
    if backend == "docker":
        return DockerWorkspace(
            server_image=image_tag,
            host_port=host_port,
            platform=detect_platform(),
        )
    if backend == "sail":
        from openhands.workspace import SailWorkspace

        size_value = os.getenv("SAILBOX_SIZE", "s")
        if size_value not in {"s", "m", "l"}:
            raise ValueError("SAILBOX_SIZE must be 's', 'm', or 'l'")
        size = "l" if size_value == "l" else "m" if size_value == "m" else "s"
        return SailWorkspace(
            server_image=image_tag,
            target_type="source",
            app_name=os.getenv("SAIL_APP_NAME", "codescout-agent-server"),
            sailbox_name=os.getenv("SAILBOX_NAME"),
            size=size,
            memory_limit_gib=int(os.getenv("SAILBOX_MEMORY_GIB", "8")),
            disk_limit_gib=int(os.getenv("SAILBOX_DISK_GIB", "32")),
            startup_timeout=float(os.getenv("SAIL_STARTUP_TIMEOUT", "600")),
            expected_server_git_sha=os.getenv("SAIL_EXPECTED_SERVER_GIT_SHA"),
        )
    raise ValueError("CODESCOUT_WORKSPACE must be 'docker' or 'sail'")


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

image_tag = os.getenv("CUSTOM_AGENT_SERVER_IMAGE_TAG", DEFAULT_IMAGE_TAG)
host_port = int(os.getenv("CUSTOM_AGENT_SERVER_PORT", "8024"))

llm = LLM(
    usage_id="codescout-smoke",
    model=os.getenv("LLM_MODEL", ""),
    base_url=os.getenv("LLM_BASE_URL", ""),
    api_key=SecretStr(api_key),
)

agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name), Tool(name="LocalizationFinishTool")],
    system_prompt_filename="/app/prompts_codescout/system_prompt.j2",
    include_default_tools=[],
)

with create_workspace(image_tag, host_port) as workspace:
    assert isinstance(workspace, RemoteWorkspace)
    setup_result = workspace.execute_command(
        "set -eu\n"
        "cd /workspace/project\n"
        "cat > calculator.py <<'PY'\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def subtract(a, b):\n"
        "    return a - b\n"
        "PY\n"
        "cat > README.md <<'MD'\n"
        "# Tiny calculator\n"
        "The add function should support adding a list of numbers.\n"
        "MD\n"
        "pwd && ls -la && sed -n '1,80p' calculator.py"
    )
    if setup_result.exit_code != 0:
        raise RuntimeError(f"Workspace setup failed: {setup_result.stderr}")

    received_events: list[Any] = []

    def event_callback(event: Any) -> None:
        received_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        conversation.send_message(
            "The issue is: the calculator add function should support adding a "
            "list of numbers. Localize the exact code location that needs to be "
            "changed. Submit your final answer with localization_finish."
        )
        conversation.run()

        locations = extract_locations(conversation.state.events)
        logger.info("Localization locations: %s", locations)
        if not locations:
            raise RuntimeError("LocalizationFinishTool was not called.")
        if not any(
            location["file"] is not None and location["file"].endswith("calculator.py")
            for location in locations
        ):
            raise RuntimeError(f"Expected calculator.py in locations, got {locations}")

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()
