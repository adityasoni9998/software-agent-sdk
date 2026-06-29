#!/bin/bash
# Build an Apptainer-usable agent-server image with custom CodeScout assets.
#
# This script first builds a local custom base image from this directory, then
# builds the current SDK's source-minimal agent-server image on top of it. The
# final image has an agent-server entrypoint, so it can be used directly with
# DockerWorkspace, ApptainerWorkspace(server_image=...), or apptainer pull.
#
# Usage:
#   ./build_custom_image.sh [IMAGE] [CUSTOM_TAG] [--push]
#
# Arguments:
#   IMAGE: Docker image repository/name for the final server image
#          (default: custom-agent-server)
#   CUSTOM_TAG: Tag component used by the SDK docker builder
#               (default: codescout-custom)
#   --push: Push the final short-SHA source-minimal tag after building locally

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BUILD_PY="$REPO_ROOT/openhands-agent-server/openhands/agent_server/docker/build.py"

IMAGE="custom-agent-server"
CUSTOM_TAG="codescout-custom"
PUSH=0
POSITIONAL_ARG_COUNT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --push)
      PUSH=1
      ;;
    *)
      POSITIONAL_ARG_COUNT=$((POSITIONAL_ARG_COUNT + 1))
      if [ "$POSITIONAL_ARG_COUNT" = "1" ]; then
        IMAGE="$1"
      elif [ "$POSITIONAL_ARG_COUNT" = "2" ]; then
        CUSTOM_TAG="$1"
      else
        echo "Unexpected argument: $1" >&2
        exit 2
      fi
      ;;
  esac
  shift
done

TARGET="${TARGET:-source-minimal}"
PLATFORMS="${PLATFORMS:-linux/amd64}"
BASE_TAG="${BASE_TAG:-custom-base-image:${CUSTOM_TAG}}"
SHORT_SHA="$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD)"
FINAL_TAG="${IMAGE}:${SHORT_SHA}-${CUSTOM_TAG}-${TARGET}"

echo "Building custom base image with custom tools, prompts, and OH_EXTRA_PYTHON_PATH..."
echo "Base tag: $BASE_TAG"
echo "Build context: $SCRIPT_DIR"
echo ""

docker build \
  -t "$BASE_TAG" \
  "$SCRIPT_DIR"

echo ""
echo "Building runnable agent-server image..."
echo "Final image repository: $IMAGE"
echo "Custom tag component: $CUSTOM_TAG"
echo "Target: $TARGET"
echo "Platforms: $PLATFORMS"
echo ""

uv run python "$BUILD_PY" \
  --base-image "$BASE_TAG" \
  --target "$TARGET" \
  --image "$IMAGE" \
  --custom-tags "$CUSTOM_TAG" \
  --platforms "$PLATFORMS" \
  --load

if [ "$PUSH" = "1" ]; then
  echo ""
  echo "Pushing final runnable image:"
  echo "  $FINAL_TAG"
  docker push "$FINAL_TAG"
fi

echo ""
echo "Runnable agent-server image built:"
echo "  $FINAL_TAG"
echo ""
echo "After pushing to a registry, use with ApptainerWorkspace:"
echo "  ApptainerWorkspace(server_image=\"$FINAL_TAG\")"
echo ""
echo "Or convert the pushed image to SIF:"
echo "  apptainer pull codescout-agent-server.sif docker://$FINAL_TAG"
