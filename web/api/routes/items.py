"""Agent Memory Hub Web Admin item route mounts."""
from __future__ import annotations

from fastapi import APIRouter

from web.api.routes import (
    item_batch,
    item_crud,
    item_exports,
    item_history,
    item_imports,
    item_maintenance,
    item_metadata,
    item_mutations,
    item_search,
)

router = APIRouter()
router.include_router(item_crud.router)
router.include_router(item_batch.router)
router.include_router(item_exports.router)
router.include_router(item_history.router)
router.include_router(item_imports.router)
router.include_router(item_maintenance.router)
router.include_router(item_metadata.router)
router.include_router(item_mutations.router)
router.include_router(item_search.router)
