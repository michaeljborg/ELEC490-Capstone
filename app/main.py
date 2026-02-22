# main.py

from fastapi import FastAPI
import asyncio

# Routers
from api import ui
from api import queue
from api import monitoring
from api import vllm

# Core components
from core.config import (
    AVAILABLE_NODES,
    NODE_POOL,
    NODE_CONCURRENCY,
)
from core.dispatcher import dispatch_loop

# Monitoring background thread starter
from api.monitoring import start_metrics_listener

app = FastAPI()


# -------------------------------------------------
# Register API Routers
# -------------------------------------------------
app.include_router(ui.router)
app.include_router(queue.router)
app.include_router(monitoring.router)
app.include_router(vllm.router)


# -------------------------------------------------
# Startup Event
# -------------------------------------------------
@app.on_event("startup")
async def startup_event():

    # Seed free-node tokens
    for node in NODE_POOL:
        for _ in range(NODE_CONCURRENCY):
            AVAILABLE_NODES.put_nowait(node)

    # Start dispatcher loop
    asyncio.create_task(dispatch_loop())

    # Start TCP metrics listener thread
    start_metrics_listener()
