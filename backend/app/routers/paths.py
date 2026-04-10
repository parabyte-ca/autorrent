from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DownloadPath
from ..schemas import DownloadPathCreate, DownloadPathOut, DownloadPathUpdate

router = APIRouter()


@router.get("/paths", response_model=list[DownloadPathOut])
def get_paths(db: Session = Depends(get_db)):
    return db.query(DownloadPath).all()


@router.post("/paths", response_model=DownloadPathOut, status_code=201)
def create_path(path: DownloadPathCreate, db: Session = Depends(get_db)):
    if path.is_default:
        db.query(DownloadPath).update({"is_default": False})
    db_path = DownloadPath(**path.model_dump())
    db.add(db_path)
    db.commit()
    db.refresh(db_path)
    return db_path


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
    return db_path


@router.delete("/paths/{path_id}")
def delete_path(path_id: int, db: Session = Depends(get_db)):
    db_path = db.query(DownloadPath).filter(DownloadPath.id == path_id).first()
    if not db_path:
        raise HTTPException(status_code=404, detail="Path not found")
    db.delete(db_path)
    db.commit()
    return {"ok": True}
