"""执行适配器：把被测链路跑成一个 observed dict，供 contract 做确定性断言。

* compile.py    语义层 intent→binding→plan 离线编译（**不含**检索层）
* retrieval.py  本体层检索（真跑 GraphRAG/ontology_traversal 等策略）
* llm.py        LLM 功能（轨迹回放 / 真实 handler / 真实 LLM）
"""
