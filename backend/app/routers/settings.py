from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Setting
from ..services.qbittorrent import test_connection
from ..services.scheduler import update_interval

router = APIRouter()

DEFAULTS: dict[str, str] = {
    "qbit_host": "localhost",
    "qbit_port": "8080",
    "qbit_username": "admin",
    "qbit_password": "",
    "qbit_category": "autorrent",
    "scan_interval_minutes": "60",
    "min_seeds": "3",
    "remove_on_complete": "false",
    "jackett_url": "",
    "jackett_api_key": "",
    "apprise_url": "",
    "default_indexer": "all",
}


def _load_settings(db: Session) -> dict[str, str]:
    db_settings = {s.key: s.value for s in db.query(Setting).all()}
    return {**DEFAULTS, **db_settings}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return _load_settings(db)


@router.put("/settings")
def update_settings(updates: dict, db: Session = Depends(get_db)):
    for key, value in updates.items():
        setting = db.query(Setting).filter(Setting.key == key).first()
        str_val = str(value) if value is not None else ""
        if setting:
            setting.value = str_val
        else:
            db.add(Setting(key=key, value=str_val))
    db.commit()

    if "scan_interval_minutes" in updates:
        try:
            update_interval(int(updates["scan_interval_minutes"]))
        except Exception:
            pass

    return _load_settings(db)


@router.post("/settings/test-qbit")
def test_qbit(db: Session = Depends(get_db)):
    success, message = test_connection()
    return {"success": success, "message": message}
