from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import QualityProfile, Setting, WatchlistItem
from ..schemas import ProfileCreate, ProfileOut, ProfileUpdate

router = APIRouter()

_DEFAULT_KEY = "default_profile_id"


def _out(db: Session, p: QualityProfile) -> dict:
    count = db.query(func.count(WatchlistItem.id)).filter(WatchlistItem.profile_id == p.id).scalar()
    return {
        "id": p.id, "name": p.name, "quality": p.quality, "codec": p.codec,
        "max_size_mb": p.max_size_mb, "preferred_groups": p.preferred_groups,
        "prefer_season_packs": bool(p.prefer_season_packs),
        "fallback_quality": p.fallback_quality,
        "fallback_after_hours": p.fallback_after_hours,
        "watchlist_count": count or 0,
    }


def _name_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    q = db.query(QualityProfile).filter(func.lower(QualityProfile.name) == name.strip().lower())
    if exclude_id is not None:
        q = q.filter(QualityProfile.id != exclude_id)
    return q.first() is not None


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return [_out(db, p) for p in db.query(QualityProfile).order_by(func.lower(QualityProfile.name)).all()]


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db)):
    if _name_taken(db, body.name):
        raise HTTPException(status_code=409, detail="A profile with that name already exists.")
    p = QualityProfile(**{**body.model_dump(), "name": body.name.strip()})
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(db, p)


@router.put("/profiles/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: int, body: ProfileUpdate, db: Session = Depends(get_db)):
    p = db.query(QualityProfile).filter(QualityProfile.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if _name_taken(db, changes["name"], exclude_id=p.id):
            raise HTTPException(status_code=409, detail="A profile with that name already exists.")
    for field, value in changes.items():
        setattr(p, field, value)
    db.commit()
    db.refresh(p)
    return _out(db, p)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a profile; shows using it keep its values as their own settings."""
    p = db.query(QualityProfile).filter(QualityProfile.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Profile not found")

    items = db.query(WatchlistItem).filter(WatchlistItem.profile_id == p.id).all()
    for item in items:
        item.quality = p.quality
        item.codec = p.codec
        item.max_size_mb = p.max_size_mb
        item.preferred_groups = p.preferred_groups
        item.prefer_season_packs = p.prefer_season_packs
        item.profile_id = None

    default = db.query(Setting).filter(Setting.key == _DEFAULT_KEY).first()
    if default and default.value == str(p.id):
        default.value = ""

    db.delete(p)
    db.commit()
    return {"ok": True, "detached": len(items)}
