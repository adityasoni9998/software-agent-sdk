# Custom Tools with Remote Agent Server

This example demonstrates how to use custom tools with a remote agent server by
building a runnable source-minimal agent-server image that includes your tool
implementations and exposes them through `OH_EXTRA_PYTHON_PATH`.

## Overview

When using a remote agent server, custom tools must be available in the server's
Python environment. This example shows the complete workflow for:

1. **Defining custom tools** that log structured data to a JSON file
2. **Building a runnable agent-server image** from a custom base layer that
   includes your tools and sets `OH_EXTRA_PYTHON_PATH`
3. **Using `DockerWorkspace`, `SailWorkspace`, or `ApptainerWorkspace`** with
   the final image
4. **Using dynamic tool registration** to make tools available at runtime
5. **Verifying the results** by reading the logged data back from the workspace

## Use Cases

This pattern is useful for:

- **Structured data collection**: Define tools like `log_data`, `record_metric`,
  or `track_event` to collect structured data during agent runs
- **Custom integrations**: Tools that interact with external systems (APIs, databases, etc.)
- **Domain-specific operations**: Business logic tools specific to your application
- **Downstream processing**: Collected data can be used to generate reports, trigger workflows, etc.

## Architecture

```
┌─────────────────┐         ┌──────────────────────────┐
│   SDK Client    │         │   Remote Agent Server    │
│                 │         │ (source-minimal image)   │
│  - Define tools │◄────────┤                          │
│  - Send tasks   │   API   │  - Custom tools in       │
│  - Get results  │         │    OH_EXTRA_PYTHON_PATH  │
│                 │         │  - Dynamic registration  │
└─────────────────┘         │  - Tool execution        │
                            │  - JSON file output      │
                            └──────────────────────────┘
```

## Files in This Example

- **`custom_tools/log_data.py`**: Example custom tool for logging structured data to JSON
- **`custom_tools/localization_finish.py`**: CodeScout localization finish tool
- **`prompts_codescout/system_prompt.j2`**: CodeScout system prompt override
- **`Dockerfile`**: Simple Dockerfile that copies custom tools and prompts into the base image
- **`build_custom_image.sh`**: Script to build the runnable source-minimal
  agent-server image
- **`main.py`**: SDK script demonstrating the full workflow
- **`main_codescout_smoke.py`**: No-LLM smoke test for CodeScout image contents
- **`main_codescout_llm.py`**: LLM-backed test for dynamic registration and tool execution
- **`README.md`**: This documentation

## The Custom Tool

The example includes a `LogDataTool` that logs structured data to a JSON file:

```python
# Define the action (input to the tool)
class LogDataAction(Action):
    message: str  # The log message
    level: LogLevel  # Enum: debug, info, warning, error
    data: dict[str, Any]  # Additional structured data

# Define the observation (output from the tool)
class LogDataObservation(Observation):
    success: bool
    log_file: str
    entry_count: int

# Auto-register the tool when module is imported
register_tool("LogDataTool", LogDataTool)
```

## How It Works

### 1. Tool Implementation (`custom_tools/log_data.py`)

The tool defines:
- **Action**: Input structure (what the LLM provides)
- **Observation**: Output structure (what the LLM receives back)
- **Executor**: Logic that writes to `/tmp/agent_data.json`
- **Auto-registration**: `register_tool()` call at module level

### 2. Dockerfile

The Dockerfile is very simple:
```dockerfile
FROM nikolaik/python-nodejs:python3.13-nodejs22-slim

# Copy custom tools and prompt assets into the custom base image
COPY custom_tools /app/custom_tools
COPY prompts_codescout /app/prompts_codescout

# Tell the agent server where to find external Python modules
ENV OH_EXTRA_PYTHON_PATH="/app"
```

This creates the custom base layer with your custom tools and tells the agent
server where to import them from. `build_custom_image.sh` then builds the
current SDK's source-minimal agent-server image on top of this layer.

### CodeScout Localization Tool

This directory also includes the `LocalizationFinishTool` recovered from the
older custom image:

- Server import path: `custom_tools.localization_finish`
- Tool name for `Agent(tools=...)`: `LocalizationFinishTool`
- User-facing tool title: `localization_finish`
- System prompt path inside the image:
  `/app/prompts_codescout/system_prompt.j2`

Use the same import path on the client before creating the conversation:

```python
import custom_tools.localization_finish  # noqa: F401

agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name), Tool(name="LocalizationFinishTool")],
    system_prompt_filename="/app/prompts_codescout/system_prompt.j2",
    include_default_tools=[],
)
```

The client and server must agree on the Python module path for custom action and
observation classes. If the server imports `custom_tools.localization_finish`
but the client imports the same file as
`platoon.codescout.custom_tools.localization_finish`, Python creates separate
class identities for the same logical schema. Prefer making
`custom_tools.localization_finish` importable on the client, for example by
putting `plugins/codescout/platoon/codescout` on `PYTHONPATH`, instead of
rewriting `__module__` or aliasing `sys.modules`.

Build the runnable source-minimal image with:

```bash
cd examples/02_remote_agent_server/06_custom_tool
./build_custom_image.sh --push
```

By default, the script publishes an immutable commit tag and a stable tag:

```text
docker.io/adityasoni8/codescout-agent-server-sail-workspace:<short-sha>-codescout-sail-source-minimal
docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal
```

Set the positional `IMAGE` and `CUSTOM_TAG` arguments to publish elsewhere.

Use that final tag directly with `ApptainerWorkspace`:

```python
from openhands.workspace import ApptainerWorkspace

with ApptainerWorkspace(
    server_image="docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal",
) as workspace:
    ...
```

Or convert it to a SIF first:

```bash
apptainer pull codescout-agent-server.sif \
  docker://docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal
```

```python
with ApptainerWorkspace(sif_file="codescout-agent-server.sif") as workspace:
    ...
```

Use the same final tag directly with `DockerWorkspace`:

```python
from openhands.workspace import DockerWorkspace

with DockerWorkspace(
    server_image="docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal",
) as workspace:
    ...
```

To test the CodeScout image contents without spending LLM tokens:

```bash
cd examples/02_remote_agent_server/06_custom_tool
CUSTOM_AGENT_SERVER_IMAGE_TAG=docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal \
  uv run python main_codescout_smoke.py
```

That smoke test starts `DockerWorkspace` from the runnable custom agent-server
image and verifies:

- `/app/custom_tools/localization_finish.py` exists in the image
- `/app/prompts_codescout/system_prompt.j2` exists in the image
- the example exits with `EXAMPLE_COST: 0`

To test dynamic registration and tool execution with an LLM:

```bash
cd examples/02_remote_agent_server/06_custom_tool
CUSTOM_AGENT_SERVER_IMAGE_TAG=docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal \
LLM_BASE_URL=... \
LLM_MODEL=... \
LLM_API_KEY=... \
  uv run python main_codescout_llm.py
```

To run the same CodeScout prompt and toolset in a Sailbox, install the
optional dependency and authenticate once:

```bash
uv sync --all-packages --extra sail
sail auth login
```

Then select the Sail backend. Because this script builds the `source-minimal`
target, `SailWorkspace` starts the source agent-server entrypoint:

```bash
CODESCOUT_WORKSPACE=sail \
CUSTOM_AGENT_SERVER_IMAGE_TAG=docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal \
LLM_BASE_URL=... \
LLM_MODEL=... \
LLM_API_KEY=... \
  uv run python main_codescout_llm.py
```

The published Docker Hub repository must be public for Sail's registry import.

### 3. Dynamic Tool Registration

When creating a conversation, the SDK:
1. Collects tool module qualnames from the client's registry
2. Sends them to the server in the conversation creation request
3. Server imports those modules, triggering auto-registration
4. Tools become available for agent execution

### 4. Build Script (`build_custom_image.sh`)

The script:
- Builds the custom base layer
- Builds the source-minimal agent-server image on top
- Optionally pushes immutable and stable runnable image tags

### 5. SDK Script (`main.py`)

The SDK script:
- Creates an agent with the custom tool specified
- Sends a task that uses the custom tool
- Agent executes on the remote server with access to the custom tool
- **Reads the JSON log file back** to verify the tool worked

## Running the Example

### Prerequisites

- Docker installed and running
- OpenHands SDK installed
- `LLM_API_KEY` environment variable set

### Steps

1. **Navigate to this directory**:
   ```bash
   cd examples/02_remote_agent_server/06_custom_tool
   ```

2. **Run the example**:
   ```bash
   python main.py
   ```

The script will build the final runnable source-minimal image. Use that image
with `DockerWorkspace`, `SailWorkspace`, `ApptainerWorkspace(server_image=...)`,
or convert it to a SIF and pass `sif_file=...`.

### Expected Output

```
🔍 Checking for custom base image: custom-base-image:latest
📦 Building custom base image with custom tools...
✅ Custom base image built successfully!
🚀 Building and starting agent server with custom tools...
📋 Conversation ID: <id>
📝 Sending task to analyze files and log findings...
🚀 Running conversation...
✅ Task completed!
📊 Logged Data Summary:
================================================================================
Found 3 log entries:

Entry 1:
  Timestamp: 2024-01-15T10:30:00.000000+00:00
  Level: info
  Message: Starting analysis of Python files
  Data: {"directory": "/workspace"}

Entry 2:
  Timestamp: 2024-01-15T10:30:05.000000+00:00
  Level: info
  Message: Found interesting pattern
  Data: {"file": "example.py", "pattern": "decorator usage"}

Entry 3:
  Timestamp: 2024-01-15T10:30:10.000000+00:00
  Level: warning
  Message: Potential issue detected
  Data: {"file": "utils.py", "line": 42, "issue": "missing error handling"}

================================================================================
✅ Example completed successfully!
```

## Creating Your Own Custom Tools

### 1. Define Your Tool

Create a new Python file in `custom_tools/`:

```python
from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor, register_tool

class MyAction(Action):
    # Define your input fields
    param1: str
    param2: int

class MyObservation(Observation):
    # Define your output fields
    result: str
    success: bool

class MyExecutor(ToolExecutor[MyAction, MyObservation]):
    def __call__(self, action: MyAction, conversation=None):
        # Implement your tool logic
        return MyObservation(result="...", success=True)

class MyTool(ToolDefinition[MyAction, MyObservation]):
    @classmethod
    def create(cls, conv_state, **params):
        executor = MyExecutor()
        return [cls(
            description="Tool description",
            action_type=MyAction,
            observation_type=MyObservation,
            executor=executor,
        )]

# Auto-register
register_tool("MyTool", MyTool)
```

### 2. Update the Dockerfile

No changes needed! The Dockerfile already copies all of `custom_tools/` and sets
`OH_EXTRA_PYTHON_PATH=/app` so the agent server can import the package.

### 3. Use Your Tool

In your SDK script:

```python
from openhands.workspace import DockerWorkspace

# Use DockerWorkspace with your runnable custom agent-server image
with DockerWorkspace(
    server_image="docker.io/adityasoni8/codescout-agent-server-sail-workspace:codescout-sail-source-minimal",
    host_port=8010,
) as workspace:
    # Create agent with your custom tool
    tools = get_default_tools(enable_browser=False)
    tools.append(Tool(name="MyTool"))
    
    agent = Agent(llm=llm, tools=tools, ...)
    # ... rest of your code
```

## Related Documentation

- [Standalone Custom Tools Example](../../01_standalone_sdk/02_custom_tools.py)
- [Tool Definition API](../../../openhands-sdk/openhands/sdk/tool/)
- [Agent Server API](../../../openhands-agent-server/)
- [Dynamic Tool Registration](https://github.com/OpenHands/software-agent-sdk/pull/1129)

## Questions?

If you have questions or run into issues:
1. Check the [SDK documentation](https://docs.all-hands.dev/sdk/)
2. Review existing tools in `openhands-tools/`
3. Open an issue on [GitHub](https://github.com/OpenHands/software-agent-sdk/issues)
