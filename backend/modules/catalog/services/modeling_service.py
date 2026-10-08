"""Data Modeling Service — visual model management with ER diagrams.

Provides:
- Model CRUD (logical models over physical schemas)
- Table positioning (canvas layout)
- Relation management (override auto-detected relations)
- Calculated fields
- M-Schema generation for NL2SQL
"""

import json
import logging
import time
from typing import Optional

from backend.common.db.metadata_db import get_metadata_conn

logger = logging.getLogger(__name__)


def _gen_id():
    return int(time.time() * 1000000)


class ModelingService:
    """Data modeling service."""

    # ── Model CRUD ─────────────────────────────────────────────────

    def list_models(self, workspace_id: int, datasource_id: int = None) -> list:
        """List data models."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                where = ["workspace_id = %s"]
                params = [workspace_id]
                if datasource_id:
                    where.append("datasource_id = %s")
                    params.append(datasource_id)
                cur.execute(
                    f"SELECT * FROM adh_data_models WHERE {' AND '.join(where)} ORDER BY updated_at DESC",
                    params
                )
                models = cur.fetchall()
                # Attach counts
                for m in models:
                    cur.execute("SELECT COUNT(*) as cnt FROM adh_model_tables WHERE model_id = %s", (m["id"],))
                    m["table_count"] = cur.fetchone()["cnt"]
                    cur.execute("SELECT COUNT(*) as cnt FROM adh_model_relations WHERE model_id = %s", (m["id"],))
                    m["relation_count"] = cur.fetchone()["cnt"]
                return models
        finally:
            conn.close()

    def get_model(self, model_id: int) -> Optional[dict]:
        """Get model with tables, relations, and calculated fields."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM adh_data_models WHERE id = %s", (model_id,))
                model = cur.fetchone()
                if not model:
                    return None

                cur.execute("SELECT * FROM adh_model_tables WHERE model_id = %s ORDER BY table_name", (model_id,))
                model["tables"] = cur.fetchall()

                cur.execute("SELECT * FROM adh_model_relations WHERE model_id = %s", (model_id,))
                model["relations"] = cur.fetchall()

                cur.execute("SELECT * FROM adh_calculated_fields WHERE model_id = %s", (model_id,))
                model["calculated_fields"] = cur.fetchall()

                return model
        finally:
            conn.close()

    def create_model(self, data: dict) -> int:
        """Create a new data model."""
        model_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_data_models
                       (id, workspace_id, datasource_id, model_name, description, is_active, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (model_id, data.get("workspace_id", 0), data["datasource_id"],
                     data["model_name"], data.get("description", ""),
                     data.get("is_active", 1), data.get("created_by"))
                )
                conn.commit()
                return model_id
        finally:
            conn.close()

    def update_model(self, model_id: int, data: dict) -> bool:
        """Update model metadata."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["model_name", "description", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(model_id)
                cur.execute(f"UPDATE adh_data_models SET {', '.join(fields)} WHERE id = %s", params)
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_model(self, model_id: int) -> bool:
        """Delete model and all related data."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_calculated_fields WHERE model_id = %s", (model_id,))
                cur.execute("DELETE FROM adh_model_relations WHERE model_id = %s", (model_id,))
                cur.execute("DELETE FROM adh_model_tables WHERE model_id = %s", (model_id,))
                cur.execute("DELETE FROM adh_data_models WHERE id = %s", (model_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Model Tables ───────────────────────────────────────────────

    def add_table(self, model_id: int, data: dict) -> int:
        """Add a table to the model."""
        table_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_model_tables
                       (id, model_id, table_name, display_name, business_desc, is_visible, position_x, position_y)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (table_id, model_id, data["table_name"],
                     data.get("display_name", data["table_name"]),
                     data.get("business_desc", ""), data.get("is_visible", 1),
                     data.get("position_x", 0), data.get("position_y", 0))
                )
                conn.commit()
                return table_id
        finally:
            conn.close()

    def update_table(self, table_id: int, data: dict) -> bool:
        """Update table config (position, display name, etc.)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["display_name", "business_desc", "is_visible", "position_x", "position_y"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(table_id)
                cur.execute(f"UPDATE adh_model_tables SET {', '.join(fields)} WHERE id = %s", params)
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Model Relations ────────────────────────────────────────────

    def add_relation(self, model_id: int, data: dict) -> int:
        """Add a relation to the model."""
        rel_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_model_relations
                       (id, model_id, source_table, source_column, target_table, target_column,
                        join_type, relation_type, description, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (rel_id, model_id, data["source_table"], data["source_column"],
                     data["target_table"], data["target_column"],
                     data.get("join_type", "INNER"), data.get("relation_type", "1:N"),
                     data.get("description", ""), data.get("is_active", 1))
                )
                conn.commit()
                return rel_id
        finally:
            conn.close()

    def update_relation(self, rel_id: int, data: dict) -> bool:
        """Update a relation."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["source_table", "source_column", "target_table", "target_column",
                            "join_type", "relation_type", "description", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(rel_id)
                cur.execute(f"UPDATE adh_model_relations SET {', '.join(fields)} WHERE id = %s", params)
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_relation(self, rel_id: int) -> bool:
        """Delete a relation."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_model_relations WHERE id = %s", (rel_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Calculated Fields ──────────────────────────────────────────

    def add_calculated_field(self, model_id: int, data: dict) -> int:
        """Add a calculated field."""
        field_id = _gen_id()
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO adh_calculated_fields
                       (id, model_id, table_name, field_name, display_name, expression, data_type, description, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (field_id, model_id, data["table_name"], data["field_name"],
                     data.get("display_name", data["field_name"]),
                     data["expression"], data.get("data_type", "number"),
                     data.get("description", ""), data.get("is_active", 1))
                )
                conn.commit()
                return field_id
        finally:
            conn.close()

    def update_calculated_field(self, field_id: int, data: dict) -> bool:
        """Update a calculated field."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                fields = []
                params = []
                for key in ["field_name", "display_name", "expression", "data_type", "description", "is_active"]:
                    if key in data:
                        fields.append(f"{key} = %s")
                        params.append(data[key])
                if not fields:
                    return False
                params.append(field_id)
                cur.execute(f"UPDATE adh_calculated_fields SET {', '.join(fields)} WHERE id = %s", params)
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def delete_calculated_field(self, field_id: int) -> bool:
        """Delete a calculated field."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM adh_calculated_fields WHERE id = %s", (field_id,))
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    # ── Auto-detect relations ──────────────────────────────────────

    def auto_detect_relations(self, datasource_id: int) -> list:
        """Auto-detect table relations from metadata (adh_table_relations)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT source_table, source_column, target_table, target_column,
                       relation_type, join_type, description
                       FROM adh_table_relations
                       WHERE datasource_id = %s AND is_active = 1""",
                    (datasource_id,)
                )
                return cur.fetchall()
        finally:
            conn.close()

    # ── M-Schema generation ────────────────────────────────────────

    def generate_mschema(self, model_id: int) -> str:
        """Generate M-Schema string for NL2SQL from model config."""
        model = self.get_model(model_id)
        if not model:
            return ""

        datasource_id = model["datasource_id"]
        tables = model.get("tables", [])
        relations = model.get("relations", [])
        calc_fields = model.get("calculated_fields", [])

        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                lines = []
                for t in tables:
                    tname = t["table_name"]
                    display = t.get("display_name") or tname
                    bdesc = t.get("business_desc", "")

                    # Get column metadata
                    cur.execute(
                        """SELECT column_name, data_type, column_comment, business_desc
                           FROM adh_column_metadata
                           WHERE datasource_id = %s AND table_name = %s AND is_active = 1
                           ORDER BY column_name""",
                        (datasource_id, tname)
                    )
                    columns = cur.fetchall()

                    line = f"Table: {tname}"
                    if display != tname:
                        line += f" ({display})"
                    if bdesc:
                        line += f" -- {bdesc}"
                    lines.append(line)
                    lines.append("Columns:")

                    for col in columns:
                        col_line = f"  - {col['column_name']} ({col['data_type']})"
                        comment = col.get("business_desc") or col.get("column_comment", "")
                        if comment:
                            col_line += f" -- {comment}"
                        lines.append(col_line)

                    # Add calculated fields for this table
                    table_cfields = [cf for cf in calc_fields if cf["table_name"] == tname]
                    if table_cfields:
                        lines.append("Calculated Fields:")
                        for cf in table_cfields:
                            cf_line = f"  - {cf['field_name']} = {cf['expression']}"
                            if cf.get("description"):
                                cf_line += f" -- {cf['description']}"
                            lines.append(cf_line)

                    lines.append("")

                # Add relations
                if relations:
                    lines.append("Relations:")
                    for r in relations:
                        if r.get("is_active", 1):
                            desc = r.get("description", "")
                            lines.append(
                                f"  - {r['source_table']}.{r['source_column']} -> "
                                f"{r['target_table']}.{r['target_column']} "
                                f"({r.get('relation_type', '1:N')}, {r.get('join_type', 'INNER')} JOIN)"
                                f"{(' -- ' + desc) if desc else ''}"
                            )

                return "\n".join(lines)
        finally:
            conn.close()

    # ── Sync from metadata ─────────────────────────────────────────

    def sync_from_metadata(self, workspace_id: int, datasource_id: int, created_by: int = None) -> int:
        """Auto-create a model from synced metadata (tables + relations)."""
        conn = get_metadata_conn()
        try:
            with conn.cursor() as cur:
                # Get all active tables
                cur.execute(
                    "SELECT table_name, table_comment, table_business_desc FROM adh_table_info WHERE datasource_id = %s AND is_active = 1",
                    (datasource_id,)
                )
                tables = cur.fetchall()

                # Get all active relations
                cur.execute(
                    """SELECT source_table, source_column, target_table, target_column,
                       relation_type, join_type, description
                       FROM adh_table_relations WHERE datasource_id = %s AND is_active = 1""",
                    (datasource_id,)
                )
                relations = cur.fetchall()

            # Create model
            model_id = self.create_model({
                "workspace_id": workspace_id,
                "datasource_id": datasource_id,
                "model_name": f"Auto Model (DS {datasource_id})",
                "description": f"从数据源 {datasource_id} 自动同步的模型",
                "created_by": created_by,
            })

            # Add tables with auto-layout (grid)
            import math
            cols = max(1, math.ceil(math.sqrt(len(tables))))
            for i, t in enumerate(tables):
                row = i // cols
                col = i % cols
                self.add_table(model_id, {
                    "table_name": t["table_name"],
                    "display_name": t.get("table_comment", t["table_name"]),
                    "business_desc": t.get("table_business_desc", ""),
                    "position_x": col * 300,
                    "position_y": row * 200,
                })

            # Add relations
            for r in relations:
                self.add_relation(model_id, r)

            return model_id
        finally:
            conn.close()


# Singleton instance
modeling_service = ModelingService()
