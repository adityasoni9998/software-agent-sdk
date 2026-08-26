"""Smoke test the CodeScout custom-tool image without making an LLM call.

This verifies that a runnable custom source-minimal agent-server image contains
the CodeScout tool and system prompt.
"""

import os
import platform
from pathlib import Path

from openhands.sdk import get_logger
from openhands.sdk.workspace import PlatformType
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


image_tag = os.getenv("CUSTOM_AGENT_SERVER_IMAGE_TAG", DEFAULT_IMAGE_TAG)
host_port = int(os.getenv("CUSTOM_AGENT_SERVER_PORT", "8012"))

logger.info("Using custom agent-server image: %s", image_tag)

with DockerWorkspace(
    server_image=image_tag,
    host_port=host_port,
    platform=detect_platform(),
) as workspace:
    result = workspace.execute_command(
        "set -eu\n"
        "test -f /app/custom_tools/localization_finish.py\n"
        "test -f /app/prompts_codescout/system_prompt.j2\n"
        "sha256sum /app/custom_tools/localization_finish.py\n"
        "sha256sum /app/prompts_codescout/system_prompt.j2\n"
    )

    logger.info("Command completed with exit code %s", result.exit_code)
    logger.info("stdout:\n%s", result.stdout)
    if result.stderr:
        logger.info("stderr:\n%s", result.stderr)
    if result.exit_code != 0:
        raise RuntimeError(f"CodeScout image smoke test failed: {result.stderr}")

expected_prompt = Path("/app/prompts_codescout/system_prompt.j2")
logger.info("Verified CodeScout tool and prompt files, including %s", expected_prompt)
print("EXAMPLE_COST: 0")
