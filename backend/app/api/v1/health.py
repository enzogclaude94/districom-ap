from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

from app.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
def health(engine: Annotated[Engine, Depends(get_engine)]) -> JSONResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    status_code = 200 if db_status == "ok" else 503
    return JSONResponse(
        status_code=status_code, content={"status": "ok" if db_status == "ok" else "degraded", "db": db_status}
    )
