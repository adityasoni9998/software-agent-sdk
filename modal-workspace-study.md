# Modal Workspace for Remote RL Rollouts

Date: 2026-08-25

## Assessment

Replacing the legacy trainer's local Apptainer execution with Modal Sandboxes is
feasible with a relatively small OpenHands addition. A `ModalWorkspace` should
provision and manage a Modal Sandbox, then delegate workspace and conversation
traffic to the existing `RemoteWorkspace` HTTP and WebSocket implementation.

No new agent-server endpoints or SDK wire protocols are required for an MVP.

## Revision alignment

The relevant components are aligned at:

```text
43376f1868ffd702746080714a59c16d3f69ec12
```

Evidence:

- The current `software-agent-sdk` checkout is exactly that commit.
- The legacy trainer pins `openhands-sdk`, `openhands-tools`,
  `openhands-workspace`, and `openhands-agent-server` to that full revision in
  `/home/adityabs/hyperion2/plugins/issue-resolution-legacy/pyproject.toml`.
- The trainer's image naming uses short SHA `43376f1` in
  `/home/adityabs/hyperion2/plugins/issue-resolution-legacy/platoon/issue_resolution/tasks.py`.
- A sampled cached trainer SIF contains
  `OPENHANDS_BUILD_GIT_SHA=43376f1868ffd702746080714a59c16d3f69ec12`.
- The same SIF starts the source agent-server with
  `/agent-server/.venv/bin/python -m openhands.agent_server`.

The local SDK, trainer-side dependencies, and sampled agent-server image are
therefore aligned.

## Proposed architecture

Add `openhands.workspace.modal.ModalWorkspace` as a subclass of the existing
`RemoteWorkspace`:

```text
Local RL process
    |
    | Modal control API: create/terminate
    v
Modal Sandbox
    `-- openhands-agent-server :8000
            ^
            | HTTPS and WebSocket through a Modal tunnel
            | X-Session-API-Key authentication
            `-- ModalWorkspace / RemoteConversation
```

The provisioning flow should be:

1. Look up or create a stable Modal App using `modal.App.lookup(...)`.
2. Import the existing composite evaluation image with
   `modal.Image.from_registry(server_image)`.
3. Generate a unique agent-server session key and inject it as
   `OH_SESSION_API_KEYS_0`.
4. Create a Sandbox with:
   - An explicit agent-server command
   - `encrypted_ports=[8000]`
   - Configurable CPU and memory requests and limits
   - A lifetime of approximately 3600 seconds
   - Optional name, tags, region, and inbound CIDR allowlist
5. Read the public URL from `sandbox.tunnels()[8000].url`.
6. Assign the URL to `RemoteWorkspace.host` and the generated key to `api_key`.
7. Poll `/health`, then optionally validate `/server_info`.
8. Use inherited `RemoteWorkspace` operations for commands, files, git, and
   conversations.
9. Terminate and detach the Sandbox idempotently during cleanup.

The inherited implementation already supports:

- Remote command execution through the agent-server bash API
- File upload and download
- Git changes and diffs
- Remote conversations over HTTP and WebSockets

Modal tunnels support both HTTP and WebSocket traffic, so the existing client
protocol fits without adaptation.

References:

- [Modal Sandbox networking](https://modal.com/docs/guide/sandbox-networking)
- [OpenHands RemoteWorkspace](../openhands-sdk/openhands/sdk/workspace/remote/base.py)
- [OpenHands ApptainerWorkspace](../openhands-workspace/openhands/workspace/apptainer/workspace.py)

## Image strategy

RL rollouts should use the existing per-instance composite image produced by:

```python
agent_server_image_for_instance(instance)
```

This produces image references in the following form:

```text
docker.io/adityasoni8/eval-agent-server:
43376f1-<instance-image-name>-source-minimal
```

The composite image is preferable to starting directly from
`instance["image_name"]` because it contains both `/testbed` and the matching
OpenHands agent-server.

For the MVP, `modal.Image.from_registry()` is sufficient. Before a large
training run, the images should be pre-imported and published as named Modal
images. This avoids many concurrent workers triggering cold image imports.
Modal recommends separating image builds from Sandbox creation and treats an
external registry tag as immutable after caching it.

The imported image's Docker `ENTRYPOINT` must be cleared with
`setup_dockerfile_commands=["ENTRYPOINT []"]`. Modal appends the Sandbox
command to a registry entrypoint rather than replacing it, which otherwise
causes the agent-server to receive its own executable path as an invalid
argument. `ModalWorkspace` supplies the complete binary or source command after
clearing the entrypoint.

Reference:

- [Separating Sandbox image builds](https://modal.com/docs/guide/sandboxes#separating-image-builds-from-sandbox-creation)

## OpenHands changes

Add the following files and package surface:

```text
openhands-workspace/openhands/workspace/modal/__init__.py
openhands-workspace/openhands/workspace/modal/workspace.py
tests/workspace/test_modal_workspace.py
```

Update `openhands-workspace/openhands/workspace/__init__.py` to expose
`ModalWorkspace` lazily. The Modal Python package should preferably be an
optional `openhands-workspace` dependency so importing `openhands.workspace`
does not require Modal.

The initial model should expose fields similar to:

```python
class ModalWorkspace(RemoteWorkspace):
    working_dir: str = "/workspace"
    host: str = ""
    server_image: str
    app_name: str = "openhands-rl"
    sandbox_id: str | None = None
    sandbox_name: str | None = None
    environment_name: str | None = None
    agent_server_port: int = 8000
    timeout: int = 3600
    startup_timeout: float = 600.0
    cpu: float | tuple[float, float] | None = None
    memory: int | tuple[int, int] | None = None
    region: str | list[str] | None = None
    inbound_cidr_allowlist: list[str] | None = None
    forward_env: list[str] = []
    keep_alive: bool = False
    expected_server_git_sha: str | None = None
```

The agent-server command should be explicit rather than relying on imported
container entrypoint behavior. For the current `source-minimal` images it is:

```text
/agent-server/.venv/bin/python -m openhands.agent_server \
  --host 0.0.0.0 --port 8000
```

`ModalWorkspace` should support attachment to an already-running Sandbox using
`modal.Sandbox.from_id()`. Reattachment must require the original agent-server
session key as well as the Sandbox ID.

## Trainer changes

Change
`/home/adityabs/hyperion2/plugins/issue-resolution-legacy/platoon/issue_resolution/rollout.py`
so `prepare_workspace()` selects a backend from configuration rather than
constructing `ApptainerWorkspace` unconditionally.

A Modal rollout would use:

```python
workspace = ModalWorkspace(
    server_image=agent_server_image_for_instance(instance),
    working_dir="/workspace",
    app_name="openhands-rl",
    timeout=3600,
    cpu=(1.0, 4.0),
    memory=(2048, 8192),
    expected_server_git_sha=SDK_SHA,
)
```

The existing repository setup can remain unchanged:

1. Copy `/testbed` to `/workspace/<repo>`.
2. Fetch git data.
3. Check out the instance commit.
4. Pass the `ModalWorkspace` to `SWEBenchEnv`.

The workspace constructor is synchronous, and the rollout already calls
`prepare_workspace()` in an executor, so the Modal control-plane calls will not
block the rollout event loop.

## Authentication and networking

For the MVP, use an encrypted Modal tunnel plus a unique OpenHands session key:

- Modal provides TLS and a cryptographically random tunnel URL.
- The agent-server independently authenticates every API request using
  `X-Session-API-Key`.
- `RemoteConversation` sends its session key on the WebSocket connection.

Modal Connect Tokens provide an additional authentication layer but cannot be
used transparently by the current OpenHands client. REST calls would need an
`Authorization` header in addition to `X-Session-API-Key`, while WebSocket calls
would need the Modal token as a header, query parameter, or cookie. A connect
token embedded in the workspace base URL would also conflict with current URL
construction.

Connect Token support should therefore be a later hardening change with an
additive transport-auth abstraction shared by `RemoteWorkspace` and
`RemoteConversation`. An inbound CIDR allowlist can be used in the meantime if
the trainer has stable egress addresses.

## Lifecycle behavior

Cleanup must cover every successful or partially successful provisioning stage:

- If image import or Sandbox creation fails, report the original failure.
- If tunnel discovery or health checking fails, terminate the Sandbox.
- If the rollout is cancelled, terminate and detach the Sandbox.
- Repeated cleanup calls must be harmless.
- With `keep_alive=False`, call `terminate()` and then `detach()`.
- With `keep_alive=True`, call only `detach()` and expose the Sandbox ID needed
  for later attachment.
- Reset and close the inherited HTTP client during cleanup.

Modal Sandboxes default to a five-minute lifetime and permit up to 24 hours.
The trainer's rollout timeout exceeds 20 minutes, and setup commands can take
several more minutes, so a lifetime around one hour is a safer initial default.

An idle timeout is less useful for active rollouts because an open tunnel TCP
connection, including the conversation WebSocket, counts as activity.

Reference:

- [Modal Sandbox lifecycle and timeouts](https://modal.com/docs/guide/sandboxes#timeouts)

## Scale and cost considerations

The current trainer configuration allows:

```text
32 concurrent workflow workers * 8 rollouts per group = 256 rollouts
```

This makes several controls necessary:

- A trainer-side semaphore or rate limiter for Sandbox creation
- Retry with bounded exponential backoff for transient Modal failures
- A configured maximum concurrent Sandbox count below the account quota
- Image pre-importing before the training job begins
- Explicit resource requests and upper limits
- Tags containing run ID, task ID, rollout ID, and SDK SHA
- A reconciliation command that lists and terminates orphaned run-tagged
  Sandboxes after a crashed trainer

Modal currently documents plan-level container limits of 100 for Starter and
5,000 for Team, so 256 concurrent rollouts require a suitable plan or reduced
trainer concurrency.

Modal's default Sandbox request is 0.125 physical CPU and 128 MiB of memory.
That is likely too small for an agent-server plus arbitrary repository tests.
The existing Modal evaluation runtime requests four CPUs. The RL workspace
should begin with an explicit request and limit, then tune requests using actual
usage measurements.

Sandbox CPU and memory are billed per second based on the greater of requested
and actual usage, so excessively high guaranteed requests also increase cost.

References:

- [Modal plan limits and pricing](https://modal.com/pricing)
- [Modal Sandbox resources and pricing](https://modal.com/docs/guide/sandbox-resources)
- [Modal CPU and memory configuration](https://modal.com/docs/guide/resources)

## Existing behavior to correct

The current setup command is:

```python
ENV_SETUP_COMMANDS = ["export PIP_CACHE_DIR=~/.cache/pip"]
```

Every `execute_command()` call starts an independent remote shell, so this
export does not persist into later commands. If `PIP_CACHE_DIR` matters, pass it
in the Modal Sandbox environment or combine it with the command that consumes
it.

## Testing plan

### Unit tests

Mock the Modal client and verify:

- App lookup arguments
- Registry image selection
- Agent-server executable selection
- Session-key generation and environment injection
- Resource, timeout, port, tags, and network arguments
- Tunnel URL propagation into `host`
- Attachment via Sandbox ID
- Required API key during attachment
- Health-check success and failure
- Server SHA validation
- Cleanup after failures at each provisioning stage
- Idempotent termination and detachment
- `keep_alive` behavior

### Single-Sandbox integration test

Launch one real Sandbox and verify:

1. `/health` succeeds.
2. `/server_info` reports the expected full SDK SHA.
3. `execute_command()` returns stdout, stderr, and exit status correctly.
4. Long-running commands and command timeouts work.
5. File upload and download work.
6. Git changes and diffs work under `/workspace`.
7. A complete `RemoteConversation` receives WebSocket events.
8. Cleanup leaves no running Sandbox.

### Rollout validation

1. Run one complete SWE-Smith rollout.
2. Confirm `/testbed` setup and commit checkout.
3. Confirm patch extraction and reward evaluation.
4. Exercise timeout and cancellation paths.
5. Run 8 concurrent rollouts.
6. Run 32 concurrent rollouts.
7. Only then test the 256-rollout target while measuring:
   - Sandbox creation throughput
   - Cold-start and image-import latency
   - Health-check latency
   - CPU and memory usage
   - Transient create failures
   - Orphaned Sandbox count
   - End-to-end rollout cost

## Recommended implementation sequence

1. Implement the minimal `ModalWorkspace` provisioning and cleanup lifecycle.
2. Add mocked unit tests and lazy package export.
3. Add trainer backend selection and per-instance image mapping.
4. Run the single-Sandbox integration test.
5. Add explicit SHA validation through `/server_info`.
6. Add creation throttling, tags, and orphan reconciliation.
7. Pre-import images and perform progressively larger rollout tests.
8. Consider Modal Connect Token integration only after the basic transport is
   stable.
