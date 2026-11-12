"""模型中心「能力序 + 能力标签」契约：排序有事实源、标签有依据、写路径不抹列。

背景（用户诉求：多配置几个大模型并按能力排序；再单独给模型能力打标——多模态/成本/评级）：
  * 旧读路径是 `ORDER BY is_default DESC, name` —— 名称字典序把旗舰与最省混成一列，
    用户与管理员看不出该选哪个；把排序写死在前端模型名清单里又会在下次登记时漂移。
    排序真值源 = `adh_llm_models.capability_rank`（1=最强，99=未定档沉底）。
  * 能力标签**不另存一份**：多模态/思考/单价本来就各有列，页面按列派生（见
    frontend/src/lib/llmModelTags.ts），避免同一概念两处落地改一处漏一处。
  * 历史缺陷（本文件顺带锁住）：platform 侧 update/set_default 用「DELETE + INSERT 重建整行」，
    而 INSERT 列清单缺 context_window / supports_vision / capability_rank —— 点一次
    「设为默认」就把全表这些列打回默认值（能力序直接消失）。

本文件不连库（纯源码/迁移护栏），因此在任何环境都能跑：
  1. 迁移把列建出来，且默认值是「未定档」而不是 0/1（新登记不猜能力）；
  2. 三条读路径都按能力序排，口径一致；
  3. 写路径能定档、能标多模态、能填单价（列不是只读死数据）；
  4. platform 侧写路径不得回潮成整行重建。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


MIND_API = "backend/modules/mind/api/model_config.py"
PLATFORM_SVC = "backend/modules/platform/services/model_config_service.py"
LLM_RES = "backend/modules/mind/execution/llm_resources.py"


# ── 1. 迁移 ────────────────────────────────────────────────────────────────

def test_migration_adds_capability_rank_with_undefaulted_rank():
    sql = _read("docker/mysql/llm_models_capability_rank_migration.sql")
    assert "ADD COLUMN capability_rank INT NOT NULL DEFAULT 99" in sql
    assert "information_schema.COLUMNS" in sql          # 幂等：先判列是否存在再 ALTER


# ── 2. 三条读路径同口径 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [MIND_API, PLATFORM_SVC, LLM_RES])
def test_read_paths_order_by_capability_rank(rel):
    src = _read(rel)
    assert re.search(r"ORDER BY capability_rank ASC, name", src), rel
    # 旧写法不得回潮：只按名称字典序（旗舰与最省混列），或让默认项置顶打断能力序
    assert not re.search(r"ORDER BY is_default DESC, name", src), rel


@pytest.mark.parametrize("rel", [MIND_API, PLATFORM_SVC])
def test_list_sql_returns_the_columns_the_ui_tags_are_derived_from(rel):
    """标签派生自 capability_rank / supports_vision / 单价：SELECT 漏列 = 页面渲成空白。"""
    src = _read(rel)
    for col in ("capability_rank", "supports_vision"):
        assert col in src, f"{rel} 未返回 {col}"
    if rel == MIND_API:                 # platform 侧 list 早带 vision，单价只在 mind 侧对外
        for col in ("input_price_per_1k", "output_price_per_1k", "currency"):
            assert col in src, f"{rel} 未返回 {col}"


# ── 3. 写路径可定档 / 可标能力 ──────────────────────────────────────────────

def test_mind_write_path_accepts_rank_vision_and_prices():
    src = _read(MIND_API)
    assert "capability_rank: int = 99" in src                  # 新建缺省=未定档
    assert "capability_rank: Optional[int] = None" in src      # 编辑可改
    assert "supports_vision: bool = False" in src              # 多模态不预设"支持"
    assert "capability_rank = %s" in src
    assert 'for price_field in ("input_price_per_1k", "output_price_per_1k", "currency")' in src


def test_platform_create_does_not_claim_vision_by_default():
    """建表默认 supports_vision=1；写入口若沿用默认，等于替新模型宣称支持图片。"""
    svc = _read(PLATFORM_SVC)
    body = svc.split("def create_llm_model(")[1].split("\ndef ")[0]
    assert "supports_vision" in body
    assert re.search(r"1 if data\.get\(\"supports_vision\"\) else 0", body)


# ── 4. 写路径不得抹列 ───────────────────────────────────────────────────────

def test_platform_write_paths_do_not_rebuild_rows():
    svc = _read(PLATFORM_SVC)
    for fn in ("update_llm_model", "set_default_model"):
        body = svc.split(f"def {fn}(")[1].split("\ndef ")[0]
        assert "DELETE FROM adh_llm_models" not in body, fn
        assert "INSERT INTO adh_llm_models" not in body, fn
    assert "UPDATE adh_llm_models SET" in svc
