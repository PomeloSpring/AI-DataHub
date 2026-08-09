"""DataCatalog Microservice - Metadata Management, Metrics, Tags, Glossary, Data Discovery.

Port: 8005
"""

import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.catalog import router as catalog_router
from .api.metadata import router as metadata_router
from .api.templates import router as templates_router
from .api.glossary import router as glossary_router
from .api.lineage import router as lineage_router
from .api.metrics import router as metrics_router
from .api.tags import router as tags_router
from .api.datasources import router as datasources_router
from .api.menu import router as menu_router
from .api.admin_compat import router as admin_compat_router
from .api.om_proxy import router as om_proxy_router
from .api.ontology import router as ontology_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(redirect_slashes=True,
    title="DataCatalog Service",
    description="Metadata management, metrics, tags, glossary, and data discovery",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(catalog_router, prefix="/api/catalog", tags=["Catalog"])
app.include_router(metadata_router, prefix="/api/admin", tags=["Metadata Admin"])
app.include_router(templates_router, prefix="/api/admin/templates", tags=["Templates Admin"])
app.include_router(glossary_router, prefix="/api/admin/terms", tags=["Glossary Admin"])
app.include_router(lineage_router, prefix="/api/admin/relations", tags=["Lineage Admin"])
app.include_router(metrics_router, prefix="/api/metrics", tags=["Metrics"])
app.include_router(tags_router, prefix="/api/tags", tags=["Tags"])
app.include_router(datasources_router, prefix="/api/datasources", tags=["Datasources"])
app.include_router(menu_router, prefix="/api/menu", tags=["Menu"])
app.include_router(admin_compat_router, prefix="/api/admin", tags=["Admin Compat"])
app.include_router(om_proxy_router, prefix="/api/catalog", tags=["OpenMetadata Proxy"])
app.include_router(ontology_router, prefix="/api/catalog/ontology", tags=["Ontology Modeling"])

# Node metrics for distributed monitoring
from services.shared.common.system_metrics import router as node_metrics_router
app.include_router(node_metrics_router, tags=["node-metrics"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "datacatalog"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
