from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Download, DownloadPath, Setting
from ..schemas import DownloadCreate
from ..services.qbittorrent import add_torrent, get_torrent_status

router = APIRouter()

DONE_STATES = {"uploading", "stalledUP", "forcedUP", "checkingUP"}
SEEDING_STATES = {"uploading", "stalledUP", "forcedUP"}
DOWNLOADING_STATES = {"downloading", "stalledDL", "forcedDL", "checkingDL", "metaDL"}


@router.get("/downloads")
def get_downloads(db: Session = Depends(get_db)):
    downloads = db.query(Download).order_by(Download.created_at.desc()).all()
    result = []

    for d in downloads:
        item = {
            "id": d.id,
            "title": d.title,
            "torrent_hash": d.torrent_hash,
            "size_bytes": d.size_bytes,
            "status": d.status,
            "download_path": d.download_path,
            "watchlist_id": d.watchlist_id,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "progress": None,
            "eta": None,
            "dlspeed": None,
        }

        if d.torrent_hash and d.status not in ("done", "error"):
            try:
                qs = get_torrent_status(d.torrent_hash)
                if qs:
                    item["progress"] = qs.get("progress")
                    item["eta"] = qs.get("eta")
                    item["dlspeed"] = qs.get("dlspeed")
                    if d.size_bytes is None and qs.get("size"):
                        d.size_bytes = qs["size"]
                    qstate = qs.get("status", "")
                    if qstate in SEEDING_STATES:
                        d.status = "seeding"
                        item["status"] = "seeding"
                    elif qstate in DOWNLOADING_STATES:
                        d.status = "downloading"
                        item["status"] = "downloading"
                    db.commit()
            except Exception:
                pass

        result.append(item)

    return result


@router.post("/downloads", status_code=201)
def add_download(payload: DownloadCreate, db: Session = Depends(get_db)):
    save_path = "/downloads"

    if payload.download_path_id:
        dp = db.query(DownloadPath).filter(DownloadPath.id == payload.download_path_id).first()
        if dp:
            save_path = dp.path
    else:
        dp = db.query(DownloadPath).filter(DownloadPath.is_default == True).first()
        if dp:
            save_path = dp.path

    settings = {s.key: s.value for s in db.query(Setting).all()}
    category = settings.get("qbit_category", "autorrent")

    try:
        info_hash = add_torrent(payload.magnet, save_path, category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add torrent: {e}")

    download = Download(
        title=payload.title,
        torrent_hash=info_hash,
        magnet_link=payload.magnet,
        status="downloading",
        download_path=save_path,
        watchlist_id=payload.watchlist_id,
    )
    db.add(download)
    db.commit()
    db.refresh(download)

    return {
        "id": download.id,
        "title": download.title,
        "torrent_hash": download.torrent_hash,
        "status": download.status,
        "download_path": download.download_path,
        "created_at": download.created_at.isoformat() if download.created_at else None,
    }


@router.delete("/downloads/{download_id}")
def delete_download(download_id: int, db: Session = Depends(get_db)):
    download = db.query(Download).filter(Download.id == download_id).first()
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")
    db.delete(download)
    db.commit()
    return {"ok": True}
