"""DataViz API -- FastAPI routers for dashboards, charts, reports, and components."""

from services.dataviz.api.dashboard import router as dashboard_router
from services.dataviz.api.chart import router as chart_router
from services.dataviz.api.report import router as report_router
from services.dataviz.api.component_data import router as component_data_router

__all__ = [
    "dashboard_router",
    "chart_router",
    "report_router",
    "component_data_router",
]
