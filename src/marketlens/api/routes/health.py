"""Health probes.

* ``/health/live``  — the process is up. Used by the container HEALTHCHECK and the App
  Service health check; must never call external services.
* ``/health/ready`` — reports version and which integrations have credentials, which is
  useful right after a deployment.
"""

from __future__ import annotations

from fastapi import APIRouter

from marketlens import __version__
from marketlens.api.dependencies import SettingsDep
from marketlens.api.schemas.common import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def ready(settings: SettingsDep) -> ReadinessResponse:
    integrations = {
        "llm": settings.llm_is_configured,
        "blob_storage": settings.storage.is_configured,
        **settings.data_providers.configured_providers(),
    }
    return ReadinessResponse(
        version=__version__,
        environment=settings.environment,
        llm_provider=settings.llm.provider,
        integrations=integrations,
    )
