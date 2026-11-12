"""「工作站 → 云空间」改名的口径与菜单同步契约（源码级守卫）。

口径（需求确认 2026-11-04，两轮纠偏后的最终版）：
  * 只退役「工作站」这个**称呼**（侧栏分组标题、默认空间名后缀、「默认工作站」等）；
  * 实体名「工作空间」**不改**，默认空间名落「xx的工作空间」（不是「xx的云空间」）；
  * 「云空间」此后**专指账号级的对话产物备份层**（上云 = 备份），与归档（= 可跨会话
    复用的资产）是两件事，不得拿它当前缀去命名一个工作空间。
改名必须与菜单权限配置同批落地（menu-permission-sync §2），否则出现“页面有新名、
角色页还是旧名/配了没用”的漂移。

守卫内容：
  N1  全仓不再出现「工作站」字样（含注释，术语已退役）；
  N2  实体名「工作空间」仍在使用，且没有任何地方把空间实体叫「云空间」；
  N3  侧栏「云空间」段的资产清单已登记进迁移，且前端不再走「恒可见」例外；
  N4  迁移里的菜单 label/section/sort 与侧栏文案、顺序逐项一致；
  N5  云空间偏好（会话产物自动上云）有读写接口，默认开启；
  N6  存量空间名纠偏：「的工作站」与误改出的「的云空间」都归回「的工作空间」。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "docker" / "mysql" / "cloud_space_rename_migration.sql"
SIDEBAR = ROOT / "frontend" / "src" / "components" / "WorkspaceLayout.tsx"

SKIP_DIR = {"node_modules", "dist", "__pycache__", ".git", "venv", "runtime", "data", "logs"}


def _sources(patterns):
    here = pathlib.Path(__file__).resolve()
    for pat in patterns:
        for p in ROOT.glob(pat):
            # 本守卫文件自带旧术语（写的是口径本身），扫描时排除
            if p.resolve() == here:
                continue
            if p.is_file() and not (set(p.parts) & SKIP_DIR):
                yield p


# ── N1 术语退役 ───────────────────────────────────────────────────────────────
def test_workstation_term_is_retired():
    stale = []
    for p in _sources(["backend/**/*.py", "frontend/src/**/*.ts", "frontend/src/**/*.tsx",
                       "tests/*.py"]):
        if "工作站" in p.read_text(encoding="utf-8", errors="ignore"):
            stale.append(str(p.relative_to(ROOT)))
    assert not stale, f"「工作站」已退役为「云空间」，仍有残留: {stale}"


# ── N2 实体名不被顺手改掉 ─────────────────────────────────────────────────────
def test_workspace_entity_name_untouched():
    sidebar = SIDEBAR.read_text(encoding="utf-8")
    # 空间切换/删除的实体名仍是「工作空间」，只有分组标题换成了「云空间」
    assert 'aria-label="切换工作空间"' in sidebar
    assert "选择工作空间" in sidebar
    assert "{ section: '云空间' }" in sidebar


def test_cloud_space_never_names_a_workspace():
    """「云空间」= 账号级备份层，不得拿它命名一个工作空间（两套语义会互相打架）。

    只拦“把空间叫云空间”的句式（名字字面量与实体词组）。“会话的云空间产物副本”
    这类修饰的是**产物**，属于备份语义，不算误称。
    """
    entity_phrases = ("个人云空间", "默认云空间", "新建云空间", "云空间切换", "切换云空间",
                      "全部云空间", "本云空间", "跨云空间", "云空间维度", "云空间管理",
                      "云空间清单", "云空间内容", "云空间用量", "云空间配额")
    name_literal = re.compile(r"['\"][^'\"]*的云空间['\"]")
    offenders = []
    for p in _sources(["backend/**/*.py", "frontend/src/**/*.ts", "frontend/src/**/*.tsx"]):
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if any(ph in ln for ph in entity_phrases) or name_literal.search(ln):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {ln.strip()[:60]}")
    assert not offenders, f"空间实体名必须用「工作空间」，发现误称: {offenders}"


# ── N3/N4 菜单登记与侧栏一致 ─────────────────────────────────────────────────
def _migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_assets_menu_registered_with_cloud_space_section():
    sql = _migration_sql()
    assert "workspace:assets" in sql, "资产清单入口必须登记进 adh_menu_registry"
    row = re.search(r"VALUES \('workspace:assets',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*(\d+)",
                    sql)
    assert row, "workspace:assets 登记行格式不符（label/section/module/perm_code/ai_access/sort）"
    label, section, module, perm_code, _, sort = row.groups()
    assert (label, section, module, perm_code) == ("资产清单", "云空间", "workspace", "asset:read")

    sidebar = SIDEBAR.read_text(encoding="utf-8")
    # 侧栏文案与顺序逐项对齐（N4）
    assert f"label: '{label}'" in sidebar
    assert f"{{ section: '{section}' }}" in sidebar
    assert "always: true" not in sidebar.split("assets`")[1][:120], \
        "资产清单已改为按权限码显隐，不得保留「恒可见」例外"
    # 排序：资产清单(4) 与任务调度(5) 的相对顺序与侧栏一致
    sched = re.search(r"menu_key = 'workspace:scheduled' AND module = 'workspace'", sql)
    assert sched and f"SET sort = 5" in sql[:sched.start()] and int(sort) == 4


def test_assets_perm_code_granted_to_existing_roles():
    sql = _migration_sql()
    assert re.search(r"INSERT IGNORE INTO adh_role_perms \(role_id, perm_code, granted_at\)\s*"
                     r"SELECT r\.id, 'asset:read'", sql), \
        "新菜单必须给存量角色默认授权，否则老用户入口凭空消失"
    assert "asset:read" in sql and "adh_perm_registry" in sql, "菜单必须挂权限点，否则角色页渲染不出"


def test_system_workspace_menu_label_keeps_entity_name():
    sql = _migration_sql()
    assert "SET label = '工作空间管理'" in sql and "system:workspaces" in sql
    assert "云空间管理" not in sql, "实体名「工作空间」不参与本次改名"


# ── N6 存量空间名纠偏 ────────────────────────────────────────────────────────
def test_workspace_name_migration_restores_entity_name():
    sql = _migration_sql()
    assert "REPLACE(name, '的工作站', '的工作空间')" in sql
    assert "REPLACE(name, '的云空间', '的工作空间')" in sql, \
        "首版迁移误改出的「xx的云空间」必须纠回实体名"
    assert "REPLACE(name, '的工作站', '的云空间')" not in sql, \
        "不得再把空间名改成云空间"
    assert "is_default = 1" in sql, "纠偏只动自动创建的默认空间，不碰用户自取的名字"
    ws_api = (ROOT / "backend" / "modules" / "auth" / "api" / "workspaces.py").read_text(encoding="utf-8")
    assert "'}的工作空间\", \"个人工作空间(随用户自动创建)\"" in ws_api, \
        "新建默认空间的代码口径必须与迁移一致"


# ── N5 云空间偏好（默认开启 + 可关闭）────────────────────────────────────────
def test_cloud_sync_preference_surface_exists():
    users_api = (ROOT / "backend" / "modules" / "auth" / "api" / "users.py").read_text(encoding="utf-8")
    assert '@router.get("/me/preferences")' in users_api
    assert '@router.put("/me/preferences")' in users_api
    profile = (ROOT / "frontend" / "src" / "pages" / "Profile.tsx").read_text(encoding="utf-8")
    assert "/users/me/preferences" in profile and "cloud_sync_enabled" in profile

    cloud = (ROOT / "backend" / "modules" / "mind" / "execution" / "session_cloud.py").read_text(encoding="utf-8")
    # 无偏好行 = 默认开启（保守默认，不给存量用户回填）
    assert "if not row:\n        return True" in cloud
    # 关闭只影响推送，已有云端副本仍可回落（读路径不看开关）
    ensure = cloud.split("def ensure_local_workspace", 1)[1]
    assert "cloud_sync_enabled" not in ensure.split("def ")[0]

    sql = _migration_sql()
    assert "adh_user_preferences" in sql and "cloud_sync_enabled TINYINT(1) NOT NULL DEFAULT 1" in sql
