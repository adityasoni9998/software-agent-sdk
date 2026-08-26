"""Opt-in integration smoke test for a real Sailbox."""

import os

import pytest


pytest.importorskip("sail")

from openhands.workspace import SailWorkspace  # noqa: E402


@pytest.mark.skipif(
    os.getenv("RUN_SAIL_INTEGRATION") != "1",
    reason="Set RUN_SAIL_INTEGRATION=1 to create a billable Sailbox.",
)
def test_sail_workspace_command_smoke():
    with SailWorkspace(
        server_image=os.getenv(
            "SAIL_SERVER_IMAGE",
            "ghcr.io/openhands/agent-server:latest-python",
        ),
        app_name=os.getenv("SAIL_APP_NAME", "openhands-agent-server-test"),
        size="s",
        memory_limit_gib=8,
        disk_limit_gib=32,
    ) as workspace:
        result = workspace.execute_command(
            "printf 'hello from sailbox\\n'",
            cwd=workspace.working_dir,
        )

        assert result.exit_code == 0
        assert result.stdout == "hello from sailbox\n"
        assert workspace.get_server_info()["version"]
