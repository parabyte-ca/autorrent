import shutil
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DownloadPath, Setting
from ..schemas import DownloadPathCreate, DownloadPathOut, DownloadPathUpdate
from ..services.disk_space import is_low, threshold_bytes

router = APIRouter()


def _threshold(db: Session) -> int:
    row = db.query(Setting).filter(Setting.key == "low_disk_warn_gb").first()
    return threshold_bytes({"low_disk_warn_gb": row.value if row else "10"})


def _with_disk_usage(dp: DownloadPath, threshold: int = 0) -> dict:
    """Return a dict suitable for DownloadPathOut, with disk-usage fields populated."""
    out = {
        "id": dp.id,
        "name": dp.name,
        "path": dp.path,
        "is_default": dp.is_default,
        "free_bytes": None,
        "total_bytes": None,
        "low_space": False,
    }
    try:
        usage = shutil.disk_usage(dp.path)
        out["free_bytes"] = usage.free
        out["total_bytes"] = usage.total
        out["low_space"] = is_low(usage.free, threshold)
    except Exception:
        pass
    return out


@router.get("/paths", response_model=list[DownloadPathOut])
def get_paths(db: Session = Depends(get_db)):
    threshold = _threshold(db)
    return [_with_disk_usage(dp, threshold) for dp in db.query(DownloadPath).all()]


@router.post("/paths", response_model=DownloadPathOut, status_code=201)
def create_path(path: DownloadPathCreate, db: Session = Depends(get_db)):
    if path.is_default:
        db.query(DownloadPath).update({"is_default": False})
    db_path = DownloadPath(**path.model_dump())
    db.add(db_path)
    db.commit()
    db.refresh(db_path)
    return _with_disk_usage(db_path, _threshold(db))


@router.put("/paths/{path_id}", response_model=DownloadPathOut)
def update_path(path_id: int, path: DownloadPathUpdate, db: Session = Depends(get_db)):
    db_path = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
    if not db_path:
        raise HTTPException(status_code=404, detail="Path not found")
    updates = path.model_dump(exclude_unset=True)
    if updates.get("is_default"):
        db.query(DownloadPath).update({"is_default": False})
    for field, value in updates.items():
        setattr(db_path, field, value)
    db.commit()
    db.refresh(db_path)
    return _with_disk_usage(db_path, _threshold(db))


@router.delete("/paths/{path_id}")
def delete_path(path_id: int, db: Session = Depends(get_db)):
    db_path = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
    if not db_path:
        raise HTTPException(status_code=404, detail="Path not found")
    db.delete(db_path)
    db.commit()
    return {"ok": True}
