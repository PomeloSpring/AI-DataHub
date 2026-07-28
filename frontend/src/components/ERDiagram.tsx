import { useState, useCallback, useMemo, useEffect, useRef } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Connection,
  Edge,
  Node,
  MarkerType,
  Position,
  Handle,
  NodeProps,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Database, Link, GripVertical, X, Plus, Trash2 } from 'lucide-react';

// ── Types ──────────────────────────────────────────────────────────────

interface Relation {
  id: number;
  datasource_id: number;
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
  relation_type: string;
  join_type: string;
  description: string;
  is_active: boolean;
}

/** A single field mapping row (source_col → target_col) */
interface FieldMapping {
  source_column: string;
  target_column: string;
}

interface ERDiagramProps {
  tables: { table_name: string; table_comment: string }[];
  relations: Relation[];
  onRelationCreate: (relation: Partial<Relation>) => void;
  onRelationDelete: (id: number) => void;
  onRelationUpdate?: (id: number, relation: Partial<Relation>) => void;
}

// ── Helpers ────────────────────────────────────────────────────────────

/** Parse comma-separated column string into array */
function parseColumns(col: string): string[] {
  return col.split(',').map((c) => c.trim()).filter(Boolean);
}

/** Build comma-separated string from field mappings */
function buildColumnString(mappings: FieldMapping[], side: 'source' | 'target'): string {
  return mappings
    .map((m) => (side === 'source' ? m.source_column : m.target_column).trim())
    .filter(Boolean)
    .join(', ');
}

/** Split a relation into individual field mapping pairs */
function splitRelationToMappings(r: Relation): FieldMapping[] {
  const srcCols = parseColumns(r.source_column);
  const tgtCols = parseColumns(r.target_column);
  const maxLen = Math.max(srcCols.length, tgtCols.length);
  const mappings: FieldMapping[] = [];
  for (let i = 0; i < maxLen; i++) {
    mappings.push({
      source_column: srcCols[i] || '',
      target_column: tgtCols[i] || '',
    });
  }
  return mappings;
}

// ── Custom Table Node ──────────────────────────────────────────────────

function TableNode({ data }: NodeProps<any>) {
  const relations = (data.relations || []) as Relation[];

  const relatedColumns = useMemo(() => {
    const cols = new Set<string>();
    for (const r of relations) {
      if (r.source_table === data.label) {
        parseColumns(r.source_column).forEach((c) => cols.add(c));
      }
      if (r.target_table === data.label) {
        parseColumns(r.target_column).forEach((c) => cols.add(c));
      }
    }
    return Array.from(cols).sort();
  }, [relations, data.label]);

  return (
    <div className="bg-card border-2 border-border rounded-lg shadow-lg min-w-[180px] max-w-[260px]">
      <Handle type="target" position={Position.Left} className="!bg-primary !w-3 !h-3" />
      <Handle type="source" position={Position.Right} className="!bg-primary !w-3 !h-3" />

      <div className="flex items-center gap-2 px-3 py-2 bg-primary/10 border-b rounded-t-lg">
        <Database className="h-4 w-4 text-primary shrink-0" />
        <span className="font-semibold text-sm truncate">{data.label}</span>
      </div>

      {data.comment && (
        <div className="px-3 py-1 text-xs text-muted-foreground border-b truncate">
          {data.comment}
        </div>
      )}

      {relatedColumns.length > 0 ? (
        <div className="px-3 py-2 space-y-0.5">
          {relatedColumns.map((col: string) => (
            <div key={col} className="flex items-center gap-1 text-xs">
              <Link className="h-3 w-3 text-primary/60 shrink-0" />
              <span className="font-mono truncate">{col}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-3 py-2 text-xs text-muted-foreground italic">拖拽连接点创建关联</div>
      )}
    </div>
  );
}

// ── Custom Edge ────────────────────────────────────────────────────────

function RelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  markerEnd,
  style,
}: {
  id: string;
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  data: { relation: Relation; onEdgeClick: (e: React.MouseEvent, edgeId: string, relation: Relation) => void };
  markerEnd: any;
  style: any;
}) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
  });

  const relation = data?.relation;
  if (!relation) return null;

  const mappings = splitRelationToMappings(relation);

  return (
    <>
      <path
        id={id}
        d={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: relation.is_active ? 'hsl(var(--primary))' : 'hsl(var(--muted-foreground))',
          opacity: relation.is_active ? 1 : 0.4,
          strokeWidth: 2,
        }}
        className="cursor-pointer"
      />
      <EdgeLabelRenderer>
        <div
          className="absolute bg-card border rounded-md px-2 py-1 shadow-md pointer-events-auto nodrag nopan cursor-pointer hover:border-primary transition-colors"
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          onClick={(e) => data?.onEdgeClick?.(e, id, relation)}
        >
          {mappings.map((m, i) => (
            <div key={i} className="flex items-center gap-0.5 text-[10px] leading-tight">
              <span className="font-mono text-primary">{m.source_column}</span>
              <span className="text-muted-foreground mx-0.5">→</span>
              <span className="font-mono text-primary">{m.target_column}</span>
            </div>
          ))}
          <div className="flex items-center gap-1 mt-0.5">
            <Badge variant="outline" className="text-[9px] px-1 py-0 leading-tight">
              {relation.relation_type}
            </Badge>
            <span className="text-[9px] text-muted-foreground">{relation.join_type}</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

// ── Multi-field mapping form ───────────────────────────────────────────

function FieldMappingForm({
  mappings,
  onChange,
}: {
  mappings: FieldMapping[];
  onChange: (mappings: FieldMapping[]) => void;
}) {
  const addRow = () => onChange([...mappings, { source_column: '', target_column: '' }]);
  const removeRow = (idx: number) => {
    if (mappings.length <= 1) return;
    onChange(mappings.filter((_, i) => i !== idx));
  };
  const updateRow = (idx: number, field: 'source_column' | 'target_column', value: string) => {
    const updated = mappings.map((m, i) => (i === idx ? { ...m, [field]: value } : m));
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-medium">字段映射</Label>
        <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={addRow}>
          <Plus className="h-3 w-3 mr-1" /> 添加字段
        </Button>
      </div>
      {mappings.map((m, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            placeholder="源字段"
            value={m.source_column}
            onChange={(e) => updateRow(i, 'source_column', e.target.value)}
            className="h-7 text-xs flex-1"
          />
          <span className="text-muted-foreground text-xs shrink-0">→</span>
          <Input
            placeholder="目标字段"
            value={m.target_column}
            onChange={(e) => updateRow(i, 'target_column', e.target.value)}
            className="h-7 text-xs flex-1"
          />
          {mappings.length > 1 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 shrink-0 text-destructive hover:text-destructive"
              onClick={() => removeRow(i)}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Relation config panel (shared for create & edit) ───────────────────

function RelationConfigPanel({
  title,
  sourceTable,
  targetTable,
  relationType,
  joinType,
  description,
  mappings,
  onRelationTypeChange,
  onJoinTypeChange,
  onDescriptionChange,
  onMappingsChange,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  title: string;
  sourceTable: string;
  targetTable: string;
  relationType: string;
  joinType: string;
  description: string;
  mappings: FieldMapping[];
  onRelationTypeChange: (v: string) => void;
  onJoinTypeChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onMappingsChange: (m: FieldMapping[]) => void;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
}) {
  const canSubmit = mappings.some((m) => m.source_column.trim() && m.target_column.trim());

  return (
    <div className="bg-card border rounded-xl shadow-xl p-5 w-[460px] space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold flex items-center gap-2">
          <Link className="h-4 w-4 text-primary" />
          {title}
        </h3>
        <Button variant="ghost" size="sm" onClick={onCancel}>✕</Button>
      </div>

      <div className="flex items-center gap-2 p-2 bg-muted rounded-lg text-sm">
        <Badge variant="default">{sourceTable}</Badge>
        <span className="text-muted-foreground">→</span>
        <Badge variant="secondary">{targetTable}</Badge>
      </div>

      <FieldMappingForm mappings={mappings} onChange={onMappingsChange} />

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label className="text-xs">关系类型</Label>
          <Select value={relationType} onValueChange={onRelationTypeChange}>
            <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="1:1">1:1 一对一</SelectItem>
              <SelectItem value="1:N">1:N 一对多</SelectItem>
              <SelectItem value="N:N">N:N 多对多</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">JOIN 类型</Label>
          <Select value={joinType} onValueChange={onJoinTypeChange}>
            <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="INNER">INNER JOIN</SelectItem>
              <SelectItem value="LEFT">LEFT JOIN</SelectItem>
              <SelectItem value="RIGHT">RIGHT JOIN</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">描述（可选）</Label>
        <Input
          placeholder="如: 订单关联用户"
          value={description}
          onChange={(e) => onDescriptionChange(e.target.value)}
          className="h-8"
        />
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>取消</Button>
        <Button size="sm" onClick={onSubmit} disabled={!canSubmit}>{submitLabel}</Button>
      </div>
    </div>
  );
}

// ── Inner component ────────────────────────────────────────────────────

const nodeTypes: Record<string, React.ComponentType<any>> = { tableNode: TableNode };
const edgeTypes: Record<string, React.ComponentType<any>> = { relationEdge: RelationEdge };

function ERDiagramInner({ tables, relations, onRelationCreate, onRelationDelete, onRelationUpdate }: ERDiagramProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [sidebarSearch, setSidebarSearch] = useState('');

  // Create mode state
  const [pendingConnection, setPendingConnection] = useState<{
    source: string; target: string;
  } | null>(null);
  const [createMappings, setCreateMappings] = useState<FieldMapping[]>([{ source_column: '', target_column: '' }]);
  const [createRelType, setCreateRelType] = useState('1:N');
  const [createJoinType, setCreateJoinType] = useState('INNER');
  const [createDesc, setCreateDesc] = useState('');

  // Edit mode state
  const [editingRelation, setEditingRelation] = useState<Relation | null>(null);
  const [editMappings, setEditMappings] = useState<FieldMapping[]>([]);
  const [editRelType, setEditRelType] = useState('1:N');
  const [editJoinType, setEditJoinType] = useState('INNER');
  const [editDesc, setEditDesc] = useState('');

  const reactFlow = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement>(null);

  const canvasTableNames = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);

  // Sidebar: tables not yet on canvas
  const sidebarTables = useMemo(() => {
    const q = sidebarSearch.toLowerCase();
    return tables.filter(
      (t) =>
        !canvasTableNames.has(t.table_name) &&
        (t.table_name.toLowerCase().includes(q) || (t.table_comment || '').toLowerCase().includes(q)),
    );
  }, [tables, canvasTableNames, sidebarSearch]);

  // Track which tables set is currently loaded to detect datasource switches
  const tablesKey = useMemo(
    () => tables.map((t) => t.table_name).sort().join(','),
    [tables],
  );
  const prevTablesKeyRef = useRef(tablesKey);

  // Reset canvas when tables set changes (datasource switch)
  useEffect(() => {
    if (tablesKey !== prevTablesKeyRef.current) {
      prevTablesKeyRef.current = tablesKey;
      setNodes([]);
      setEdges([]);
    }
  }, [tablesKey]);

  // Auto-load tables involved in existing relations (first render or after reset)
  useEffect(() => {
    if (nodes.length > 0) return;
    if (relations.length === 0) return;

    const involvedTableNames = new Set<string>();
    for (const r of relations) {
      involvedTableNames.add(r.source_table);
      involvedTableNames.add(r.target_table);
    }

    const involvedTables = tables.filter((t) => involvedTableNames.has(t.table_name));
    if (involvedTables.length === 0) return;

    const COLS = Math.ceil(Math.sqrt(involvedTables.length));
    const NODE_W = 220;
    const NODE_H = 120;
    const GAP_X = 80;
    const GAP_Y = 60;

    const initialNodes: Node[] = involvedTables.map((t, i) => ({
      id: t.table_name,
      type: 'tableNode',
      position: {
        x: (i % COLS) * (NODE_W + GAP_X),
        y: Math.floor(i / COLS) * (NODE_H + GAP_Y),
      },
      data: {
        label: t.table_name,
        comment: t.table_comment || '',
        relations: relations.filter(
          (r) => r.source_table === t.table_name || r.target_table === t.table_name,
        ),
      },
    }));

    setNodes(initialNodes);
  }, [nodes.length, relations]);

  // Rebuild edges when nodes or relations change
  useEffect(() => {
    const nodeIds = new Set(nodes.map((n) => n.id));
    const visibleEdges = relations
      .filter((r) => nodeIds.has(r.source_table) && nodeIds.has(r.target_table))
      .map((r) => ({
        id: `e-${r.id}`,
        source: r.source_table,
        target: r.target_table,
        type: 'relationEdge' as const,
        data: {
          relation: r,
          onEdgeClick: handleEdgeClick,
        },
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        style: { strokeWidth: 2 },
        animated: r.is_active,
      }));
    setEdges(visibleEdges);
  }, [nodes, relations]);

  // Update node relation data when relations change
  useEffect(() => {
    setNodes((nds) =>
      nds.map((n) => ({
        ...n,
        data: {
          ...n.data,
          relations: relations.filter(
            (r) => r.source_table === n.id || r.target_table === n.id,
          ),
        },
      })),
    );
  }, [relations]);

  // Drag from sidebar
  const onDragStart = useCallback((e: React.DragEvent, tableName: string) => {
    e.dataTransfer.setData('application/reactflow', tableName);
    e.dataTransfer.effectAllowed = 'move';
  }, []);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const tableName = e.dataTransfer.getData('application/reactflow');
      if (!tableName || canvasTableNames.has(tableName)) return;

      const table = tables.find((t) => t.table_name === tableName);
      if (!table) return;

      const position = reactFlow.screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });

      setNodes((nds) => [
        ...nds,
        {
          id: tableName,
          type: 'tableNode',
          position,
          data: {
            label: tableName,
            comment: table.table_comment || '',
            relations: relations.filter(
              (r) => r.source_table === tableName || r.target_table === tableName,
            ),
          },
        },
      ]);
    },
    [tables, relations, canvasTableNames, reactFlow, setNodes],
  );

  // ── Create flow ─────────────────────────────────────────────────────

  const onConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target && connection.source !== connection.target) {
        setPendingConnection({ source: connection.source, target: connection.target });
        setCreateMappings([{ source_column: '', target_column: '' }]);
        setCreateRelType('1:N');
        setCreateJoinType('INNER');
        setCreateDesc('');
      }
    },
    [],
  );

  const handleCreateRelation = () => {
    if (!pendingConnection) return;
    const validMappings = createMappings.filter((m) => m.source_column.trim() && m.target_column.trim());
    if (validMappings.length === 0) return;

    onRelationCreate({
      source_table: pendingConnection.source,
      source_column: buildColumnString(validMappings, 'source'),
      target_table: pendingConnection.target,
      target_column: buildColumnString(validMappings, 'target'),
      relation_type: createRelType,
      join_type: createJoinType,
      description: createDesc.trim(),
      is_active: true,
    });

    setPendingConnection(null);
  };

  // ── Edit flow ───────────────────────────────────────────────────────

  function handleEdgeClick(_: React.MouseEvent, _edgeId: string, relation: Relation) {
    setEditingRelation(relation);
    setEditMappings(splitRelationToMappings(relation));
    setEditRelType(relation.relation_type || '1:N');
    setEditJoinType(relation.join_type || 'INNER');
    setEditDesc(relation.description || '');
  }

  const handleUpdateRelation = () => {
    if (!editingRelation || !onRelationUpdate) return;
    const validMappings = editMappings.filter((m) => m.source_column.trim() && m.target_column.trim());
    if (validMappings.length === 0) return;

    onRelationUpdate(editingRelation.id, {
      source_column: buildColumnString(validMappings, 'source'),
      target_column: buildColumnString(validMappings, 'target'),
      relation_type: editRelType,
      join_type: editJoinType,
      description: editDesc.trim(),
    });

    setEditingRelation(null);
  };

  const handleDeleteEditingRelation = () => {
    if (!editingRelation) return;
    onRelationDelete(editingRelation.id);
    setEditingRelation(null);
  };

  // ── Node management ─────────────────────────────────────────────────

  const removeNode = useCallback(
    (tableName: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== tableName));
    },
    [setNodes],
  );

  return (
    <div className="flex h-full border rounded-lg overflow-hidden">
      {/* Sidebar */}
      <div className="w-[220px] border-r bg-muted/30 flex flex-col shrink-0 min-h-0">
        <div className="p-2 border-b shrink-0">
          <Input
            placeholder="搜索表名…"
            value={sidebarSearch}
            onChange={(e) => setSidebarSearch(e.target.value)}
            className="h-8 text-xs"
          />
        </div>
        <ScrollArea className="flex-1 min-h-0">
          <div className="p-2 space-y-1">
            {sidebarTables.length === 0 && (
              <div className="text-xs text-muted-foreground text-center py-6 px-2">
                {tables.length === 0 ? '暂无表数据' : '所有表已在画布上'}
              </div>
            )}
            {sidebarTables.map((t) => (
              <div
                key={t.table_name}
                className="flex items-center gap-2 px-2 py-1.5 rounded border bg-card cursor-grab hover:bg-accent transition-colors text-xs"
                draggable
                onDragStart={(e) => onDragStart(e, t.table_name)}
              >
                <GripVertical className="h-3 w-3 text-muted-foreground shrink-0" />
                <Database className="h-3 w-3 text-primary shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{t.table_name}</div>
                  {t.table_comment && (
                    <div className="text-muted-foreground truncate">{t.table_comment}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
        <div className="p-2 border-t text-[10px] text-muted-foreground">
          拖拽表到画布 · 从 ⚪ 拖到 ⚪ 创建关联
        </div>
      </div>

      {/* Canvas */}
      <div
        ref={wrapperRef}
        className="flex-1 bg-background relative"
        onDragOver={onDragOver}
        onDrop={onDrop}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView={nodes.length > 0}
          fitViewOptions={{ padding: 0.3 }}
          defaultEdgeOptions={{
            type: 'relationEdge',
            markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
          }}
          connectionLineStyle={{ strokeWidth: 2, stroke: 'hsl(var(--primary))' }}
        >
          <Background gap={20} size={1} />
          <Controls />
          <MiniMap
            nodeColor="hsl(var(--primary))"
            maskColor="hsl(var(--background) / 0.7)"
            className="!bg-card !border-border"
          />
        </ReactFlow>

        {/* Remove node buttons */}
        {nodes.map((n) => (
          <button
            key={`rm-${n.id}`}
            className="absolute z-10 bg-destructive text-destructive-foreground rounded-full w-5 h-5 flex items-center justify-center text-xs hover:bg-destructive/80 shadow"
            style={{ left: n.position.x + 200, top: n.position.y - 8 }}
            onClick={() => removeNode(n.id)}
            title={`移除 ${n.id}`}
          >
            <X className="h-3 w-3" />
          </button>
        ))}

        {/* Empty state */}
        {nodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="text-center text-muted-foreground">
              <Database className="h-12 w-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">从左侧拖拽表到画布</p>
              <p className="text-xs mt-1">已有关系的表会自动加载</p>
            </div>
          </div>
        )}

        {/* Pending connection panel */}
        {pendingConnection && (
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50">
            <RelationConfigPanel
              title="新建关联关系"
              sourceTable={pendingConnection.source}
              targetTable={pendingConnection.target}
              relationType={createRelType}
              joinType={createJoinType}
              description={createDesc}
              mappings={createMappings}
              onRelationTypeChange={setCreateRelType}
              onJoinTypeChange={setCreateJoinType}
              onDescriptionChange={setCreateDesc}
              onMappingsChange={setCreateMappings}
              onSubmit={handleCreateRelation}
              onCancel={() => setPendingConnection(null)}
              submitLabel="创建"
            />
          </div>
        )}

        {/* Edit relation panel */}
        {editingRelation && (
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50">
            <div className="relative">
              <RelationConfigPanel
                title="编辑关联关系"
                sourceTable={editingRelation.source_table}
                targetTable={editingRelation.target_table}
                relationType={editRelType}
                joinType={editJoinType}
                description={editDesc}
                mappings={editMappings}
                onRelationTypeChange={setEditRelType}
                onJoinTypeChange={setEditJoinType}
                onDescriptionChange={setEditDesc}
                onMappingsChange={setEditMappings}
                onSubmit={handleUpdateRelation}
                onCancel={() => setEditingRelation(null)}
                submitLabel="保存"
              />
              <Button
                variant="destructive"
                size="sm"
                className="absolute -bottom-12 left-1/2 -translate-x-1/2"
                onClick={handleDeleteEditingRelation}
              >
                <Trash2 className="h-3 w-3 mr-1" />
                删除此关联
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Exported wrapper ───────────────────────────────────────────────────

export default function ERDiagram(props: ERDiagramProps) {
  return (
    <ReactFlowProvider>
      <ERDiagramInner {...props} />
    </ReactFlowProvider>
  );
}
