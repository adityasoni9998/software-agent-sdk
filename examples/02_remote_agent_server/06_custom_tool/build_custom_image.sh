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
#          (default: docker.io/adityasoni8/codescout-agent-server-modal-workspace)
#   CUSTOM_TAG: Tag component used by the SDK docker builder
#               (default: codescout-modal)
#   --push: Push the final short-SHA and stable source-minimal tags

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BUILD_PY="$REPO_ROOT/openhands-agent-server/openhands/agent_server/docker/build.py"

IMAGE="docker.io/adityasoni8/codescout-agent-server-modal-workspace"
CUSTOM_TAG="codescout-modal"
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
SHORT_SHA="$(git -C "$REPO_ROOT" rev-parse --short=7 HEAD)"
FINAL_TAG="${IMAGE}:${SHORT_SHA}-${CUSTOM_TAG}-${TARGET}"
STABLE_TAG="${IMAGE}:${CUSTOM_TAG}-${TARGET}"
if [ "$PUSH" = "1" ]; then
  BASE_TAG="${BASE_TAG:-${IMAGE}:base-${SHORT_SHA}-${CUSTOM_TAG}}"
  BUILDX_BUILDER_LABEL="${BUILDX_BUILDER:-current}"
else
  BASE_TAG="${BASE_TAG:-custom-base-image:${CUSTOM_TAG}}"
  BUILDX_BUILDER="${BUILDX_BUILDER:-default}"
  BUILDX_BUILDER_LABEL="$BUILDX_BUILDER"
fi

echo "Building custom base image with custom tools, prompts, and OH_EXTRA_PYTHON_PATH..."
echo "Base tag: $BASE_TAG"
echo "Build context: $SCRIPT_DIR"
echo ""

if [ "$PUSH" = "1" ]; then
  docker buildx build \
    --platform "$PLATFORMS" \
    --tag "$BASE_TAG" \
    --push \
    "$SCRIPT_DIR"
else
  BUILDX_BUILDER="$BUILDX_BUILDER" docker build \
    -t "$BASE_TAG" \
    "$SCRIPT_DIR"
fi

echo ""
echo "Building runnable agent-server image..."
echo "Final image repository: $IMAGE"
echo "Custom tag component: $CUSTOM_TAG"
echo "Target: $TARGET"
echo "Platforms: $PLATFORMS"
echo "Buildx builder: $BUILDX_BUILDER_LABEL"
echo ""

if [ "$PUSH" = "1" ]; then
  uv run python "$BUILD_PY" \
    --base-image "$BASE_TAG" \
    --target "$TARGET" \
    --image "$IMAGE" \
    --custom-tags "$CUSTOM_TAG" \
    --platforms "$PLATFORMS" \
    --push
else
  BUILDX_BUILDER="$BUILDX_BUILDER" uv run python "$BUILD_PY" \
    --base-image "$BASE_TAG" \
    --target "$TARGET" \
    --image "$IMAGE" \
    --custom-tags "$CUSTOM_TAG" \
    --platforms "$PLATFORMS" \
    --load
fi

if [ "$PUSH" = "1" ]; then
  echo ""
  echo "Publishing stable runnable image alias:"
  echo "  $FINAL_TAG"
  echo "  $STABLE_TAG"
  docker buildx imagetools create --tag "$STABLE_TAG" "$FINAL_TAG"
fi

echo ""
echo "Runnable agent-server image built:"
echo "  $FINAL_TAG"
if [ "$PUSH" = "1" ]; then
  echo "Stable pushed tag:"
  echo "  $STABLE_TAG"
fi
echo ""
echo "After pushing to a registry, use with ApptainerWorkspace:"
echo "  ApptainerWorkspace(server_image=\"$FINAL_TAG\")"
echo ""
echo "Or convert the pushed image to SIF:"
echo "  apptainer pull codescout-agent-server.sif docker://$FINAL_TAG"
