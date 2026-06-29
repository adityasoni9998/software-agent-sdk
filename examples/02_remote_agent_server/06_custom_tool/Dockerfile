# Dockerfile for custom base image with custom tools
#
# This Dockerfile creates a base image that includes custom tools.
# build_custom_image.sh builds the runnable source-minimal agent-server image
# on top of this base image.
#
# Usage:
#   cd examples/02_remote_agent_server/06_custom_tool
#   docker build -t custom-base-image:latest .

FROM nikolaik/python-nodejs:python3.13-nodejs22-slim

# Copy custom tools and prompt assets into the custom base image.
COPY custom_tools /app/custom_tools
COPY prompts_codescout /app/prompts_codescout

# Tell the agent server where to find external Python modules.
ENV OH_EXTRA_PYTHON_PATH="/app"
