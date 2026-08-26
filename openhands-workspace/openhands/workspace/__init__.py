"""OpenHands Workspace - Docker and container-based workspace implementations."""

from typing import TYPE_CHECKING

from openhands.sdk.workspace import PlatformType, TargetType

from .apptainer import ApptainerWorkspace
from .cloud import (
    CloneResult,
    GitProvider,
    OpenHandsCloudWorkspace,
    RepoMapping,
    RepoSource,
)
from .docker import DockerWorkspace
from .remote_api import APIRemoteWorkspace


if TYPE_CHECKING:
    from .docker import DockerDevWorkspace
    from .modal import ModalWorkspace

__all__ = [
    "APIRemoteWorkspace",
    "ApptainerWorkspace",
    "CloneResult",
    "DockerDevWorkspace",
    "DockerWorkspace",
    "GitProvider",
    "ModalWorkspace",
    "OpenHandsCloudWorkspace",
    "PlatformType",
    "RepoMapping",
    "RepoSource",
    "TargetType",
]


def __getattr__(name: str):
    """Lazy import workspace backends with optional or build-time dependencies."""
    if name == "DockerDevWorkspace":
        from .docker import DockerDevWorkspace

        return DockerDevWorkspace
    if name == "ModalWorkspace":
        try:
            from .modal import ModalWorkspace
        except ImportError as error:
            raise ImportError(
                "ModalWorkspace requires the optional Modal dependency. Install "
                "it with `pip install 'openhands-workspace[modal]'`."
            ) from error

        return ModalWorkspace
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
