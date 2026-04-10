from fastapi import APIRouter, HTTPException, Query

from ..services.indexers import search_all

router = APIRouter()


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    indexer: str = Query("all", description="Indexer: nyaa, tpb, jackett, or all"),
    quality: str = Query(None, description="Filter by quality: 4K, 1080p, 720p, 480p"),
):
    try:
        results = search_all(q, quality=quality, indexer=indexer)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
