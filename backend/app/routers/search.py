from fastapi import APIRouter, HTTPException, Query

from ..services.indexers import search_all

router = APIRouter()


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    indexer: str = Query("all", description="Indexer: nyaa, tpb, jackett, or all"),
    quality: str = Query(None, description="Filter by quality: 4K, 1080p, 720p, 480p"),
    codec: str = Query("x265", description="Codec: x265, x264, AV1, or Any"),
    filter_adult: bool = Query(True, description="Filter out adult/pornographic content"),
):
    try:
        results = search_all(
            q,
            quality=quality,
            indexer=indexer,
            codec=codec,
            filter_adult=filter_adult,
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
