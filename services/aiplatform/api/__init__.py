# AI Platform API routers
from services.aiplatform.api.agents import router as agents_router
from services.aiplatform.api.mcp_servers import router as mcp_servers_router
from services.aiplatform.api.mcp_market import router as mcp_market_router
from services.aiplatform.api.model_config import router as model_config_router
from services.aiplatform.api.model_lab import router as model_lab_router
from services.aiplatform.api.model_train import router as model_train_router
from services.aiplatform.api.embed import router as embed_router
from services.aiplatform.api.workflow import router as workflow_router
from services.aiplatform.api.brand import router as brand_router
from services.aiplatform.api.cache import router as cache_router
from services.aiplatform.api.execution_layers import router as execution_layers_router
