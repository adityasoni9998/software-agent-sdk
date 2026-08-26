# Sail Workspace for Remote OpenHands Agent Servers

Date: 2026-08-26

## Result

`SailWorkspace` can use Sail's Python SDK to import the public OpenHands OCI
image, create or attach to a Sailbox, explicitly start the agent-server, expose
its HTTP/WebSocket port, and delegate agent operations to `RemoteWorkspace`.
No agent-server protocol changes are needed.

Sail's native registry-image support is the simplest path. An extra Apptainer
layer is not needed: `sail.Image.from_registry(...)` imports a Debian/Ubuntu
image from a supported public registry and uses it as the Sailbox root
filesystem. Sail intentionally does not execute the image's `ENTRYPOINT` or
`CMD`, so the workspace starts the binary (or source entrypoint) itself.

## Runtime flow

```text
OpenHands client
  | Sail control API: create, pause, resume, terminate
  v
Sailbox created from ghcr.io/openhands/agent-server:latest-python
  `-- openhands-agent-server --host 0.0.0.0 --port 8000
        ^
        | Sail HTTPS listener + X-Session-API-Key
        `-- RemoteWorkspace / RemoteConversation
```

Creation performs these steps:

1. Resolve or create the configured Sail App.
2. Import the OpenHands image from GHCR.
3. Create a Sailbox with an HTTP listener for guest port 8000.
4. Generate an OpenHands session key and pass it only to the agent-server
   process as `OH_SESSION_API_KEYS_0`.
5. Start the agent-server explicitly because Sail ignores OCI entrypoints.
6. Resolve the Sail listener URL, poll `/health`, and optionally verify the
   image's build SHA through `/server_info`.
7. Use the inherited HTTP and WebSocket APIs for commands and conversations.
8. Terminate on cleanup, unless `keep_alive=True` preserves the Sailbox for
   later attachment by ID and session key.

The default image is public and currently resolves successfully:

```text
ghcr.io/openhands/agent-server:latest-python
```

For reproducible runs, prefer an immutable digest or an architecture-specific
commit tag. Sail caches the first build of a mutable tag; set
`force_image_build=True` to refresh it deliberately.

## Authentication needed for a real smoke test

The SDK reads `SAIL_API_KEY`, or the credential written by:

```bash
sail auth login
```

The older-looking `sail setup` wording is not the current documented command.
After authentication, first run a command-only smoke check, then the full agent
example at `examples/02_remote_agent_server/17_sail_workspace/main.py`.

## vLLM through ogma

The local host `babel-s9-24` has an RTX A6000 with about 48 GiB VRAM. `ogma` is
reachable over SSH at `128.2.205.27`, and its SSH daemon is configured with
`GatewayPorts yes`. This permits a reverse tunnel bound to a non-loopback
address:

```bash
# Start vLLM locally on babel-s9-24. The Hermes parser converts Qwen's XML
# function calls into the OpenAI-compatible tool_calls field OpenHands expects.
vllm serve Qwen/Qwen3-4B-Instruct-2507 \
  --served-model-name qwen3-instruct \
  --host 127.0.0.1 \
  --port 8001 \
  --api-key local-smoke-key \
  --max-model-len 16384 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --generation-config vllm

# Publish that local port on ogma. Keep this process alive for the test.
ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -R 0.0.0.0:18001:127.0.0.1:8001 \
  adityabs@ogma.lti.cs.cmu.edu
```

Then configure the OpenHands client with:

```bash
export LLM_API_KEY=local-smoke-key
export LLM_MODEL=openai/qwen3-instruct
export LLM_BASE_URL=http://ogma.lti.cs.cmu.edu:18001/v1
```

The Sailbox makes the LLM call, so `LLM_BASE_URL` must be reachable from the
public internet; `localhost` on this machine is not usable. Before launching a
model, verify from outside the CMU network that port 18001 is admitted by the
host/network firewall. If arbitrary inbound ports are blocked, place an HTTPS
reverse proxy on an already permitted ogma port or use a managed tunnel with
TLS and authentication. Do not expose an unauthenticated vLLM endpoint.

## Smoke-test sequence

```bash
uv sync --all-packages --extra sail
sail auth login

# Provision the public image and exercise the remote command API first.
uv run python - <<'PY'
from openhands.workspace import SailWorkspace

with SailWorkspace(size="s", memory_limit_gib=8, disk_limit_gib=32) as ws:
    result = ws.execute_command("printf 'hello from sailbox\\n'")
    assert result.exit_code == 0
    print(result.stdout, end="")
PY

# With vLLM and the reverse tunnel running, execute a real agent task. Pin the
# image to a tag built from the same SDK revision instead of mixing this checkout
# with the mutable latest-python image.
export SAIL_SERVER_IMAGE=ghcr.io/openhands/agent-server:43376f1-python-amd64
export SAIL_EXPECTED_SERVER_GIT_SHA=43376f1
uv run python examples/02_remote_agent_server/17_sail_workspace/main.py
```

This sequence was exercised end to end on 2026-08-26. Sail created the box,
OpenHands used Qwen3 to issue a `file_editor` action, the marker file was read
back through `RemoteWorkspace`, and the context manager terminated the box.

Every test should use a context manager so failures still terminate the
billable Sailbox. For debugging a failed launch, set `keep_alive=True`, record
both `sailbox_id` and the generated OpenHands `api_key`, and terminate the
Sailbox explicitly when finished.
