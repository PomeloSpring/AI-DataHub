-- Remove built-in execution layer (builtin agent framework has been removed)
-- The builtin layer was registered by execution_layer_migration.sql and
-- services/datamind/execution/registry.py (both now cleaned up).
-- This migration deletes the leftover row and its workspace bindings.

-- 1. Remove workspace bindings to the builtin layer (table may not exist in all deployments)
DELETE wrel FROM adh_workspace_execution_layers wrel
JOIN adh_execution_layers el ON el.id = wrel.execution_layer_id
WHERE el.name = 'builtin';

-- 2. Remove the builtin execution layer row itself
DELETE FROM adh_execution_layers WHERE name = 'builtin';
