"""Provision an OpenHands agent-server in a Modal Sandbox.

Setup:
    uv sync --all-packages --extra modal
    modal setup

Run with the public OpenHands image:
    uv run python examples/02_remote_agent_server/16_modal_workspace/main.py

Run with a SWE-Smith composite image:
    MODAL_SERVER_IMAGE=docker.io/adityasoni8/eval-agent-server:<tag> \
      MODAL_TARGET_TYPE=source \
      uv run python examples/02_remote_agent_server/16_modal_workspace/main.py

For a private registry image, also set MODAL_REGISTRY_SECRET_NAME to a Modal
Secret containing the registry credentials.
"""

import os

from openhands.workspace import ModalWorkspace


server_image = os.getenv(
    "MODAL_SERVER_IMAGE",
    "ghcr.io/openhands/agent-server:latest-python",
)
target_type_value = os.getenv("MODAL_TARGET_TYPE", "binary")
if target_type_value == "binary":
    target_type = "binary"
else:
    assert target_type_value == "source"
    target_type = "source"

print(f"Starting Modal workspace from {server_image}")
with ModalWorkspace(
    server_image=server_image,
    target_type=target_type,
    app_name=os.getenv("MODAL_APP_NAME", "openhands-agent-server-example"),
    working_dir=os.getenv("MODAL_WORKING_DIR", "/workspace"),
    timeout=int(os.getenv("MODAL_SANDBOX_TIMEOUT", "1800")),
    startup_timeout=float(os.getenv("MODAL_STARTUP_TIMEOUT", "600")),
    cpu=float(os.getenv("MODAL_CPU", "1")),
    memory=int(os.getenv("MODAL_MEMORY", "2048")),
    registry_secret_name=os.getenv("MODAL_REGISTRY_SECRET_NAME"),
    expected_server_git_sha=os.getenv("MODAL_EXPECTED_SERVER_GIT_SHA"),
    sandbox_tags={"purpose": "openhands-modal-workspace-example"},
    verbose=True,
) as workspace:
    print(f"Sandbox ID: {workspace.sandbox_id}")
    print(f"Agent-server URL: {workspace.host}")

    server_info = workspace.get_server_info()
    print(f"Agent-server version: {server_info['version']}")
    print(f"SDK version: {server_info['sdk_version']}")
    print(f"Build git SHA: {server_info['build_git_sha']}")

    result = workspace.execute_command(
        "printf 'Hello from Modal!\\n'; "
        "printf 'Working directory: '; pwd; "
        "if test -d /testbed; then echo 'SWE-Smith /testbed is available'; fi",
        cwd=workspace.working_dir,
    )
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    assert result.exit_code == 0, result.stderr

print("Modal Sandbox terminated successfully")
print("EXAMPLE_COST: 0")
