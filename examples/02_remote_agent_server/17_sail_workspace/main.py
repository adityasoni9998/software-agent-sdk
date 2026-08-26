"""Run an OpenHands agent task inside a Sailbox.

Setup:
    uv sync --all-packages --extra sail
    sail auth login

Required LLM settings:
    LLM_API_KEY=<key-or-local-placeholder>
    LLM_MODEL=<litellm-model-name>
    LLM_BASE_URL=<sailbox-reachable-openai-compatible-url>/v1

Run:
    uv run python examples/02_remote_agent_server/17_sail_workspace/main.py
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import SailWorkspace


llm_api_key = os.environ.get("LLM_API_KEY")
assert llm_api_key, "Set LLM_API_KEY (a placeholder is enough for local vLLM)."

llm_base_url = os.environ.get("LLM_BASE_URL")
assert llm_base_url, "Set LLM_BASE_URL to an endpoint reachable from the Sailbox."

target_type_value = os.getenv("SAIL_TARGET_TYPE", "binary")
assert target_type_value in {"binary", "source"}
target_type = "source" if target_type_value == "source" else "binary"
size_value = os.getenv("SAILBOX_SIZE", "s")
assert size_value in {"s", "m", "l"}
size = "l" if size_value == "l" else "m" if size_value == "m" else "s"

llm = LLM(
    usage_id="sail-smoke-agent",
    model=os.getenv("LLM_MODEL", "openai/qwen3-instruct"),
    base_url=llm_base_url,
    api_key=SecretStr(llm_api_key),
)
agent = get_default_agent(llm=llm, cli_mode=True)

server_image = os.getenv(
    "SAIL_SERVER_IMAGE",
    "ghcr.io/openhands/agent-server:latest-python",
)
print(f"Starting Sail workspace from {server_image}")
with SailWorkspace(
    server_image=server_image,
    target_type=target_type,
    app_name=os.getenv("SAIL_APP_NAME", "openhands-agent-server-example"),
    sailbox_name=os.getenv("SAILBOX_NAME"),
    working_dir=os.getenv("SAIL_WORKING_DIR", "/workspace"),
    size=size,
    memory_limit_gib=int(os.getenv("SAILBOX_MEMORY_GIB", "8")),
    disk_limit_gib=int(os.getenv("SAILBOX_DISK_GIB", "32")),
    startup_timeout=float(os.getenv("SAIL_STARTUP_TIMEOUT", "600")),
    expected_server_git_sha=os.getenv("SAIL_EXPECTED_SERVER_GIT_SHA"),
) as workspace:
    print(f"Sailbox ID: {workspace.sailbox_id}")
    print(f"Agent-server URL: {workspace.host}")

    server_info = workspace.get_server_info()
    print(f"Agent-server version: {server_info['version']}")
    print(f"Build git SHA: {server_info['build_git_sha']}")

    conversation = Conversation(agent=agent, workspace=workspace)
    assert isinstance(conversation, RemoteConversation)
    try:
        conversation.send_message(
            "Create /workspace/SAIL_SMOKE_TEST.txt containing exactly "
            "'OpenHands completed this task in a Sailbox.' and then finish."
        )
        conversation.run()
        result = workspace.execute_command(
            "cat /workspace/SAIL_SMOKE_TEST.txt",
            cwd=workspace.working_dir,
        )
        assert result.exit_code == 0, result.stderr
        assert result.stdout.strip() == "OpenHands completed this task in a Sailbox."
        print("OpenHands Sailbox task smoke test passed")
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()

print("Sailbox terminated successfully")
