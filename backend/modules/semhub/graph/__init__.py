"""Knowledge graph package for semhub.

原 graphservice(8011) 并入 semhub(8012) 后迁入: Oxigraph/SPARQL 知识图谱的
服务层(GraphService)与事件驱动同步引擎(GraphSyncService)。图谱是本体的只读派生视图,
与语义层同属"本体派生的只读语义层", 由同一 FastAPI app 挂载 /api/graph 提供。
"""
