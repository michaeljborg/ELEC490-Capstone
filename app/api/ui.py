# api/ui.py

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import json

from core.config import NODE_POOL, METRICS_SAMPLES_CAP

router = APIRouter()
templates = Jinja2Templates(directory="frontend")

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "gui.html",
        {
            "request": request,
            "node_pool": NODE_POOL,
            "node_pool_json": json.dumps(NODE_POOL),
            "METRICS_SAMPLES_CAP": METRICS_SAMPLES_CAP,
        },
    )