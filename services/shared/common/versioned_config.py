"""Versioned Config Store — abstract base class for versioned configuration management.

Provides a unified interface for:
- CRUD operations on configuration entries
- Automatic version snapshots on every update
- Version history retrieval
- Rollback to any previous version
- Activation (setting current active version)

Concrete implementations:
- PromptVersionStore:  adh_prompts + adh_prompt_versions        (column-mode)
- MCPServerVersionStore: adh_mcp_servers + adh_mcp_server_versions (json-blob-mode)
- SkillVersionStore:   adh_skills + adh_skill_versions           (json-blob-mode)
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from services.shared.common.db import execute_query, execute_insert, execute_write

logger = logging.getLogger(__name__)


class VersionedConfigStore(ABC):
    """Abstract base for versioned config stores (column-mode snapshots)."""

    #: When True, snapshots store the whole content as a single JSON `content`
    #: column instead of one column per content field.
    json_blob_mode: bool = False

    @property
    @abstractmethod
    def main_table(self) -> str:
        """Name of the main config table."""
        ...

    @property
    @abstractmethod
    def version_table(self) -> str:
        """Name of the version history table."""
        ...

    @property
    @abstractmethod
    def key_column(self) -> str:
        """Column name used as the config key (e.g. 'prompt_key')."""
        ...

    @property
    @abstractmethod
    def content_columns(self) -> list[str]:
        """Columns that constitute the versioned content."""
        ...

    # ── FK / key column names in the version table ───────────────────
    @property
    def id_col(self) -> str:
        return "prompt_id" if self.main_table == "adh_prompts" else "config_id"

    @property
    def vkey_col(self) -> str:
        return "prompt_key" if self.main_table == "adh_prompts" else "config_key"

    # ── Read ─────────────────────────────────────────────────────────
    def get(self, config_key: str) -> Optional[dict[str, Any]]:
        """Get the current active config entry by key."""
        rows = execute_query(
            f"SELECT * FROM {self.main_table} WHERE {self.key_column} = %s AND is_active = 1",
            (config_key,),
            fetchone=True,
        )
        if rows:
            self._deserialize_json_fields(rows)
        return rows

    def get_by_id(self, config_id: int) -> Optional[dict[str, Any]]:
        """Get config entry by primary key ID."""
        rows = execute_query(
            f"SELECT * FROM {self.main_table} WHERE id = %s",
            (config_id,),
            fetchone=True,
        )
        if rows:
            self._deserialize_json_fields(rows)
        return rows

    def list_all(self, active_only: bool = True) -> list[dict[str, Any]]:
        """List all config entries."""
        where = "WHERE is_active = 1" if active_only else ""
        rows = execute_query(f"SELECT * FROM {self.main_table} {where} ORDER BY updated_at DESC")
        for r in rows:
            self._deserialize_json_fields(r)
        return rows

    # ── Write ────────────────────────────────────────────────────────
    def create(self, data: dict[str, Any], created_by: str = "system") -> int:
        """Create a new config entry. Returns the new ID."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["created_at"] = now
        data["updated_at"] = now
        data["created_by"] = created_by
        data["version"] = 1
        data.setdefault("is_active", 1)

        columns = []
        placeholders = []
        params = []
        for k, v in data.items():
            columns.append(k)
            placeholders.append("%s")
            params.append(self._serialize_value(v))

        row_id = execute_insert(
            f"INSERT INTO {self.main_table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
            tuple(params),
        )

        # Create initial version snapshot (v1)
        self._create_version_snapshot(row_id, data.get(self.key_column, ""), 1,
                                      data.get("change_log", "Initial creation"), created_by)
        return row_id

    def update(self, config_id: int, data: dict[str, Any], updated_by: str = "system") -> bool:
        """Update a config entry. Automatically creates a version snapshot of the new state.

        The new content is written to the main table with an incremented version,
        and a snapshot of that new version is recorded in the version table so it
        can later be restored via rollback.
        """
        current = self.get_by_id(config_id)
        if not current:
            logger.warning("Config entry %d not found for update", config_id)
            return False

        config_key = current.get(self.key_column, "")
        new_version = current.get("version", 1) + 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Build update query
        fields = []
        params = []
        for key, value in data.items():
            if key in ("id", self.key_column, "created_at", "created_by", "change_log"):
                continue
            fields.append(f"{key} = %s")
            params.append(self._serialize_value(value))

        fields.append("version = %s")
        params.append(new_version)
        fields.append("updated_at = %s")
        params.append(now)
        params.append(config_id)

        execute_write(
            f"UPDATE {self.main_table} SET {', '.join(fields)} WHERE id = %s",
            tuple(params),
        )

        # Snapshot the NEW version state for future rollback
        change_log = data.get("change_log", f"Updated by {updated_by}")
        self._create_version_snapshot(config_id, config_key, new_version, change_log, updated_by)
        return True

    def delete(self, config_id: int) -> bool:
        """Soft-delete (deactivate) a config entry."""
        execute_write(
            f"UPDATE {self.main_table} SET is_active = 0, updated_at = %s WHERE id = %s",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), config_id),
        )
        return True

    def activate(self, config_id: int) -> bool:
        """Set a config entry as active (for multi-version scenarios)."""
        current = self.get_by_id(config_id)
        if not current:
            return False
        config_key = current.get(self.key_column, "")
        execute_write(
            f"UPDATE {self.main_table} SET is_active = 0 WHERE {self.key_column} = %s AND id != %s",
            (config_key, config_id),
        )
        execute_write(
            f"UPDATE {self.main_table} SET is_active = 1, updated_at = %s WHERE id = %s",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), config_id),
        )
        return True

    # ── Version history / rollback ───────────────────────────────────
    def get_versions(self, config_id: int) -> list[dict[str, Any]]:
        """Get version history for a config entry."""
        rows = execute_query(
            f"SELECT * FROM {self.version_table} WHERE {self.id_col} = %s ORDER BY version DESC",
            (config_id,),
        )
        for r in rows:
            self._deserialize_version_json_fields(r)
        return rows

    def rollback(self, config_id: int, target_version: int, rolled_by: str = "system") -> bool:
        """Rollback a config entry to a specific version (column-mode)."""
        current = self.get_by_id(config_id)
        if not current:
            return False
        config_key = current.get(self.key_column, "")

        snapshot = execute_query(
            f"SELECT * FROM {self.version_table} WHERE {self.id_col} = %s AND version = %s",
            (config_id, target_version),
            fetchone=True,
        )
        if not snapshot:
            logger.warning("Version %d not found for config %d", target_version, config_id)
            return False
        self._deserialize_version_json_fields(snapshot)

        new_version = current.get("version", 1) + 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        fields = []
        params = []
        for col in self.content_columns:
            if col in snapshot:
                fields.append(f"{col} = %s")
                params.append(self._serialize_value(snapshot[col]))
        fields.append("version = %s")
        params.append(new_version)
        fields.append("updated_at = %s")
        params.append(now)
        params.append(config_id)

        execute_write(
            f"UPDATE {self.main_table} SET {', '.join(fields)} WHERE id = %s",
            tuple(params),
        )
        self._create_version_snapshot(
            config_id, config_key, new_version,
            f"Rolled back to v{target_version} by {rolled_by}", rolled_by,
        )
        return True

    # ── Snapshot helpers ─────────────────────────────────────────────
    def _create_version_snapshot(self, config_id: int, config_key: str,
                                 version: int, change_log: str, created_by: str):
        """Create a version snapshot (column-mode: one column per content field)."""
        current = execute_query(
            f"SELECT * FROM {self.main_table} WHERE id = %s",
            (config_id,),
            fetchone=True,
        )
        if not current:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        columns = [self.id_col, self.vkey_col, "version", "change_log",
                   "created_at", "created_by", "is_current"]
        placeholders = ["%s"] * len(columns)
        params = [config_id, config_key, version, change_log, now, created_by, 1]

        for col in self.content_columns:
            columns.append(col)
            placeholders.append("%s")
            params.append(self._serialize_value(current.get(col, "")))

        execute_insert(
            f"INSERT INTO {self.version_table} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})",
            tuple(params),
        )
        execute_write(
            f"UPDATE {self.version_table} SET is_current = 0 "
            f"WHERE {self.id_col} = %s AND version != %s",
            (config_id, version),
        )

    def _deserialize_json_fields(self, row: dict[str, Any]):
        """Hook to deserialize JSON fields. Override in subclass."""
        pass

    def _deserialize_version_json_fields(self, row: dict[str, Any]):
        """Hook to deserialize JSON fields in version rows. Override in subclass."""
        pass

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Serialize value for DB storage (dict/list → JSON string)."""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value


class JsonBlobVersionStore(VersionedConfigStore):
    """Versioned store that keeps the full snapshot in a single JSON `content` column.

    Subclasses set `content_columns` (the fields packed into the blob) and
    optionally override `_deserialize_json_fields`.
    """

    json_blob_mode = True

    def _create_version_snapshot(self, config_id: int, config_key: str,
                                 version: int, change_log: str, created_by: str):
        current = execute_query(
            f"SELECT * FROM {self.main_table} WHERE id = %s",
            (config_id,),
            fetchone=True,
        )
        if not current:
            return
        content = {col: current.get(col, "") for col in self.content_columns}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute_insert(
            f"INSERT INTO {self.version_table} "
            f"(config_id, config_key, version, content, change_log, created_at, created_by, is_current) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (config_id, config_key, version, json.dumps(content, ensure_ascii=False),
             change_log, now, created_by, 1),
        )
        execute_write(
            f"UPDATE {self.version_table} SET is_current = 0 WHERE config_id = %s AND version != %s",
            (config_id, version),
        )

    def rollback(self, config_id: int, target_version: int, rolled_by: str = "system") -> bool:
        current = self.get_by_id(config_id)
        if not current:
            return False

        snapshot = execute_query(
            f"SELECT * FROM {self.version_table} WHERE config_id = %s AND version = %s",
            (config_id, target_version),
            fetchone=True,
        )
        if not snapshot:
            return False

        content = snapshot.get("content", "{}")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                content = {}

        new_version = current.get("version", 1) + 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        fields = []
        params = []
        for key, value in content.items():
            fields.append(f"{key} = %s")
            params.append(self._serialize_value(value))
        fields.append("version = %s")
        params.append(new_version)
        fields.append("updated_at = %s")
        params.append(now)
        params.append(config_id)

        execute_write(
            f"UPDATE {self.main_table} SET {', '.join(fields)} WHERE id = %s",
            tuple(params),
        )
        self._create_version_snapshot(
            config_id, current.get(self.key_column, ""), new_version,
            f"Rolled back to v{target_version} by {rolled_by}", rolled_by,
        )
        return True


# ── Prompt Version Store (column-mode) ─────────────────────────────────

class PromptVersionStore(VersionedConfigStore):
    """Versioned store for adh_prompts."""

    @property
    def main_table(self) -> str:
        return "adh_prompts"

    @property
    def version_table(self) -> str:
        return "adh_prompt_versions"

    @property
    def key_column(self) -> str:
        return "prompt_key"

    @property
    def content_columns(self) -> list[str]:
        # Must match columns present in adh_prompt_versions
        return ["system_prompt", "user_prompt_template"]

    # ── Workspace-aware read ─────────────────────────────────────────
    def get_active(self, prompt_key: str, workspace_id: int = 0) -> Optional[dict[str, Any]]:
        """Get the active prompt with workspace override support.

        Resolution order:
          1. workspace-specific row (prompt_key, workspace_id) if workspace_id != 0
          2. global default row (prompt_key, workspace_id = 0)

        Within a scope the highest-version active row wins (the table keeps one
        row per (prompt_key, workspace_id, version)).
        """
        if workspace_id:
            row = execute_query(
                "SELECT * FROM adh_prompts WHERE prompt_key = %s AND workspace_id = %s "
                "AND is_active = 1 ORDER BY version DESC LIMIT 1",
                (prompt_key, workspace_id),
                fetchone=True,
            )
            if row:
                self._deserialize_json_fields(row)
                return row
        row = execute_query(
            "SELECT * FROM adh_prompts WHERE prompt_key = %s AND workspace_id = 0 "
            "AND is_active = 1 ORDER BY version DESC LIMIT 1",
            (prompt_key,),
            fetchone=True,
        )
        if row:
            self._deserialize_json_fields(row)
        return row

    def list_filtered(self, active_only: bool = True, category: Optional[str] = None,
                      workspace_id: Optional[int] = None) -> list[dict[str, Any]]:
        """List prompts with optional category / workspace_id filters."""
        clauses = []
        params: list[Any] = []
        if active_only:
            clauses.append("is_active = 1")
        if category:
            clauses.append("category = %s")
            params.append(category)
        if workspace_id is not None:
            clauses.append("workspace_id = %s")
            params.append(workspace_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = execute_query(
            f"SELECT * FROM adh_prompts {where} "
            "ORDER BY category ASC, prompt_key ASC, workspace_id ASC, version DESC",
            tuple(params) if params else None,
        )
        for r in rows:
            self._deserialize_json_fields(r)
        return rows


# ── MCP Server Version Store (json-blob-mode) ──────────────────────────

class MCPServerVersionStore(JsonBlobVersionStore):
    """Versioned store for adh_mcp_servers.

    Requires adh_mcp_server_versions table:
        id, config_id, config_key, version, content(JSON), change_log,
        created_at, created_by, is_current
    """

    @property
    def main_table(self) -> str:
        return "adh_mcp_servers"

    @property
    def version_table(self) -> str:
        return "adh_mcp_server_versions"

    @property
    def key_column(self) -> str:
        return "name"

    @property
    def content_columns(self) -> list[str]:
        return ["transport", "url", "command", "args", "env", "tools_config", "description"]

    def _deserialize_json_fields(self, row: dict[str, Any]):
        for field in ("args", "env", "tools_config"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    pass


# ── Skill Version Store (json-blob-mode) ───────────────────────────────

class SkillVersionStore(JsonBlobVersionStore):
    """Versioned store for adh_skills.

    Requires adh_skill_versions table:
        id, config_id, config_key, version, content(JSON), change_log,
        created_at, created_by, is_current
    """

    @property
    def main_table(self) -> str:
        return "adh_skills"

    @property
    def version_table(self) -> str:
        return "adh_skill_versions"

    @property
    def key_column(self) -> str:
        return "name"

    @property
    def content_columns(self) -> list[str]:
        return ["display_name", "description", "category", "system_prompt", "skill_config"]

    def _deserialize_json_fields(self, row: dict[str, Any]):
        if isinstance(row.get("skill_config"), str):
            try:
                row["skill_config"] = json.loads(row["skill_config"])
            except (json.JSONDecodeError, TypeError):
                pass


# ── Singleton helpers ──────────────────────────────────────────────────

_prompt_store: Optional[PromptVersionStore] = None
_mcp_store: Optional[MCPServerVersionStore] = None
_skill_store: Optional[SkillVersionStore] = None


def get_prompt_store() -> PromptVersionStore:
    global _prompt_store
    if _prompt_store is None:
        _prompt_store = PromptVersionStore()
    return _prompt_store


def get_mcp_version_store() -> MCPServerVersionStore:
    global _mcp_store
    if _mcp_store is None:
        _mcp_store = MCPServerVersionStore()
    return _mcp_store


def get_skill_version_store() -> SkillVersionStore:
    global _skill_store
    if _skill_store is None:
        _skill_store = SkillVersionStore()
    return _skill_store
