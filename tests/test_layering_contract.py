"""分层契约门禁 — §0 依赖规则的 AST 静态强制（防回潮）。

依赖规则（唯一裁决口径）::

    common ← core/semantics ← modules ← processes      箭头单向，L2 横向禁止

- L0 `backend/common`   禁止 import backend 其他（既有桥接登记于 EDGE_EXCEPTIONS）；
- L1 `backend/core`/`backend/semantics` 仅可互引与引 common，禁 modules/processes/eval；
- L2 `backend/modules/*` 顶层横向 import 一律禁止（导入期解耦，含 mind 出边）；
  函数级横向 import 必须逐条登记 EDGE_EXCEPTIONS，未登记即 fail；
- L3 `backend/processes` 进程装配层，可引任意 backend 包；
- modules 禁止 import backend.processes（防止装配层反噬）；调用评测框架 backend.eval 属框架使用，允许。

白名单（显式登记）：

① `backend/eval/adapters/` 可 import modules（评测适配器）；
② `backend/core/task_runtime` 回调表对 modules 的注册式调用（"module:attr" 字符串登记，
   不产生 import 边，由 _CALLBACKS 一张表管住）；
③ `backend/modules/mind`（编排层）→ 其他 modules 的单向调用，合法方向（仅限函数级）。
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"

# 逐条登记的豁免边：(消费方, 目标前缀(backend. 之后), 归类, 理由)
# 归类结果按重构计划 Phase 3「二选一」执行——下沉 core/semantics 或经 core 注册表/接口注入；
# 下列条目为两类处理完成后的显式登记残余（函数级惰性导入），演进方向记录于理由栏。
EDGE_EXCEPTIONS = [
    # ── L0 → L1 既有桥接（迁移 backlog）──
    ("common", "core.role_service", "legacy-bridge",
     "common/auth.py 执行身份/角色解析桥接（函数级）；下沉 core 或经回调表注入列入 backlog"),
    ("common", "core.enforcer", "legacy-bridge",
     "common/auth.py 权限裁决桥接（函数级）；同上"),
    ("common", "semantics.datafusion_dialect", "legacy-bridge",
     "common/engine_client.py 方言适配（函数级）；随统一语义层（Phase 6）引擎迁移"),
    # ── L2 横向登记豁免（函数级惰性导入）──
    ("platform", "modules.mind.execution", "control-plane",
     "执行层控制面管理 SDK 执行运行时（as_bots/tool_catalog/tool_policy/manager/discovery/session_workspace）；"
     "接口注入列入 fde-evolution 演进"),
    ("platform", "modules.mind.config", "control-plane",
     "提示词管理清 prompt 缓存"),
    ("viz", "modules.mind.execution", "design-toolchain",
     "仪表盘设计域借用 AS-BOT 身份/语义查询/屏幕工具"),
    ("viz", "modules.mind.rag", "design-toolchain",
     "仪表盘设计引用业务知识检索 qmind_retriever"),
    ("catalog", "modules.mind.rag", "kb-sync",
     "本体知识库同步借道 notebook 检索 qmind_retriever"),
    ("catalog", "modules.mind.execution", "ontology-tools",
     "本体工具引用执行上下文/工具策略/执行服务"),
    ("catalog", "modules.mind.services", "ontology-tools",
     "本体问答借道 chat_service"),
    ("catalog", "modules.semhub.graph", "graph-sync",
     "本体/关系/术语同步知识图谱（graph_service/graph_sync_service）"),
    ("auth", "modules.mind.execution", "workspace-admin",
     "工作区磁盘用量/as-bot 可见资源统计"),
]


def _layer_of(rel: Path):
    """返回 (layer, module_name)。module_name 仅 L2 有（mind/platform/...）。"""
    parts = rel.parts  # 相对 backend/，如 modules/viz/api/x.py
    if parts[0] == "common":
        return "common", None
    if parts[0] == "core":
        return "core", None
    if parts[0] == "semantics":
        return "semantics", None
    if parts[0] == "eval":
        return ("eval.adapters" if len(parts) > 1 and parts[1] == "adapters" else "eval"), None
    if parts[0] == "modules" and len(parts) > 1:
        return "modules", parts[1]
    if parts[0] == "processes":
        return "processes", None
    return "other", None


def _collect_edges():
    """扫描 backend/**/*.py 的 backend.* import 边。

    返回 dict: (layer, module, target) -> {"top": [(path, line)], "lazy": [(path, line)]}
    相对导入按文件包路径解析后再参与分层判定。
    """
    edges = {}
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if "__pycache__" in rel.parts:
            continue
        layer, module = _layer_of(rel)
        if layer == "other":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pkg_parts = ["backend", *rel.parts[:-1]]

        def resolve(node: ast.ImportFrom):
            if node.level == 0:
                return node.module or ""
            # level=1 → 当前包；level=2 → 上一级……
            base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
            prefix = ".".join(base)
            return f"{prefix}.{node.module}" if node.module else prefix

        top_level = set(id(n) for n in tree.body)
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [resolve(node)]
            for target in targets:
                if not target or not target.startswith("backend"):
                    continue
                # 归一：去掉被引包自身内部的深层前缀，只保留到 backend.<pkg>[.<sub>...]
                key = (layer, module, target)
                bucket = "top" if id(node) in top_level else "lazy"
                edges.setdefault(key, {"top": [], "lazy": []})[bucket].append(
                    (str(rel), node.lineno))
    return edges


def _is_lateral(layer, module, target):
    """L2 横向边：modules.<X> → backend.modules.<Y>（Y≠X）。"""
    if layer != "modules" or not module:
        return False
    seg = target.split(".")
    return len(seg) > 2 and seg[1] == "modules" and seg[2] != module


def _exception_for(layer, module, target):
    consumer = module if layer == "modules" else layer
    for cons, prefix, *_ in EDGE_EXCEPTIONS:
        if cons != consumer:
            continue
        full = f"backend.{prefix}"
        if target == full or target.startswith(full + "."):
            return True
    return False


def _violations():
    """收集全部违规：返回 [(描述, [位置...])]。"""
    bad = []
    for (layer, module, target), locs in sorted(_collect_edges().items()):
        seg = target.split(".")
        where = locs["top"] + locs["lazy"]
        # R1: modules 禁止引用 processes（装配层反噬；eval 为评测框架，允许调用）
        if layer == "modules" and len(seg) > 1 and seg[1] == "processes":
            bad.append((f"modules.{module} 不得 import {target}", where))
            continue
        # R2: L0 common 仅可引 backend.common
        if layer == "common" and not target.startswith("backend.common"):
            if not _exception_for(layer, module, target):
                bad.append((f"common 不得 import {target}（未登记豁免）", where))
            continue
        # R3: L1 core/semantics 仅可互引与引 common（硬规则，不设豁免）
        if layer in ("core", "semantics") and seg[1] not in ("common", "core", "semantics"):
            bad.append((f"{layer} 不得 import {target}", where))
            continue
        # R4: eval 仅 adapters 可引 modules（白名单①）
        if layer == "eval" and seg[1] == "modules":
            bad.append((f"eval（非 adapters）不得 import {target}", where))
            continue
        if layer == "eval.adapters" and seg[1] not in ("common", "core", "semantics", "eval", "modules"):
            bad.append((f"eval.adapters 不得 import {target}", where))
            continue
        # R5: L2 横向
        if _is_lateral(layer, module, target):
            # 顶层横向 import 一律禁止（含 mind 出边，导入期解耦）
            if locs["top"]:
                bad.append((f"顶层横向 import 禁止: modules.{module} → {target}", locs["top"]))
            # 白名单③：mind → 其他 modules 合法（函数级）
            if module == "mind":
                continue
            if not _exception_for(layer, module, target):
                bad.append((f"L2 横向边未登记: modules.{module} → {target}", locs["lazy"]))
    return bad


def test_layering_rules_hold():
    violations = _violations()
    assert not violations, "\n".join(
        f"{msg} @ {locs}" for msg, locs in violations)


def test_whitelist_entries_are_live():
    """白名单卫生：每条豁免必须真实命中边，杜绝死条目口径漂移。"""
    used = set()
    for (layer, module, target) in _collect_edges():
        consumer = module if layer == "modules" else layer
        for entry in EDGE_EXCEPTIONS:
            cons, prefix, *_ = entry
            full = f"backend.{prefix}"
            if cons == consumer and (target == full or target.startswith(full + ".")):
                used.add(entry)
    dead = [f"{e[0]} → {e[1]}" for e in EDGE_EXCEPTIONS if e not in used]
    assert not dead, f"白名单死条目（请清理或修正归类）: {dead}"


def test_no_top_level_lateral_imports_in_modules():
    """顶层横向 import 清零（Phase 3 硬指标，含 mind 出边）。"""
    tops = []
    for (layer, module, target), locs in _collect_edges().items():
        if _is_lateral(layer, module, target) and locs["top"]:
            tops.extend(f"modules.{module} → {target} @ {p}:{ln}" for p, ln in locs["top"])
    assert not tops, "\n".join(tops)


def test_l0_l1_never_import_modules():
    """护城河硬指标：L0/L1 → modules 反向依赖必须恒为 0（白名单②走字符串，无 import 边）。"""
    bad = []
    for (layer, module, target), locs in _collect_edges().items():
        if layer in ("common", "core", "semantics") and target.split(".")[1:2] == ["modules"]:
            bad.extend(f"{layer} → {target} @ {p}:{ln}" for p, ln in locs["top"] + locs["lazy"])
    assert not bad, "\n".join(bad)
