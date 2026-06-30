"""Query API - Direct SQL execution."""
import io
import math
import pandas as pd
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from backend.api.auth import get_current_user
from backend.models.schemas import QueryRequest, UserInfo
from backend.nl2sql.sql.query_executor import execute_query

router = APIRouter()

def _sanitize_floats(obj):
    """Replace NaN/inf/-inf with None for JSON compliance."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj

@router.post("/execute")
def execute_sql(req: QueryRequest, user: UserInfo = Depends(get_current_user)):
    df, elapsed_ms, row_count = execute_query(req.sql)
    columns = list(df.columns) if not df.empty else []
    rows = df.to_dict(orient="records") if not df.empty else []
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "isoformat"): row[k] = v.isoformat()
            elif isinstance(v, bytes): row[k] = v.decode("utf-8", errors="replace")
    rows = _sanitize_floats(rows)
    return {"columns": columns, "rows": rows, "row_count": row_count, "elapsed_ms": elapsed_ms}

@router.post("/export")
def export_sql(req: QueryRequest, user: UserInfo = Depends(get_current_user)):
    df, _, _ = execute_query(req.sql)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=chatbi_export.xlsx"})
