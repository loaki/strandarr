from fastapi import APIRouter

from strandarr.web.routes import meta, observations, predictions

ROUTERS = (meta.router, observations.router, predictions.router)

router = APIRouter(prefix="/api")

for included in ROUTERS:
    router.include_router(included)
