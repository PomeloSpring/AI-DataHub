"""评测工程能力（Eval Engine）。

既定架构决策：评测是**项目内可复用的工程能力**，不是 tests/ 目录下的附属脚本。
生产侧（Celery 业务评测任务、管理页）要能正向导入本包，因此核心放这里，
**不得**反向依赖 tests/。

分层：
    contract.py  用例契约 + 确定性断言（passed 由它给，score 不参与判定）
    adapters/    各被测链路的执行适配器（compile / retrieval / llm）
    compare.py   基线对比与发布前检查
    store.py     用例 CRUD + 运行结果落库（失败抛错，不静默）
    runner.py    运行编排 + CLI
"""
