import { useCallback, useEffect, useState, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ReactFlowProvider } from '@xyflow/react';
import { Plus, Save, Trash2, Settings, List, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import DAGCanvas from './workflow-editor/DAGCanvas';
import NodePalette from './workflow-editor/NodePalette';
import NodeConfigPanel from './workflow-editor/NodeConfigPanel';
import { useWorkflowEditorStore } from './workflow-editor/store';
import type { WorkflowNode } from './workflow-editor/types';

function WorkflowEditorContent() {
  const {
    workflows, currentWorkflowId, nodes, edges, selectedNode, isDirty, isLoading,
    onNodesChange, onEdgesChange, onConnect, addNode, updateNodeData, deleteNode,
    setSelectedNode, loadWorkflows, loadWorkflow, saveWorkflow, createWorkflow, deleteWorkflow,
  } = useWorkflowEditorStore();

  const [searchParams, setSearchParams] = useSearchParams();
  const [showSidebar, setShowSidebar] = useState(true);
  const [showConfig, setShowConfig] = useState(false);
  const [newWorkflowName, setNewWorkflowName] = useState('');
  const [showNewInput, setShowNewInput] = useState(false);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  useEffect(() => { loadWorkflows(); }, []);
  useEffect(() => { const id = searchParams.get('id'); if (id) loadWorkflow(parseInt(id)); }, [searchParams]);

  const onDragStart = useCallback((event: React.DragEvent, nodeType: string) => {
    event.dataTransfer.setData('application/reactflow', nodeType);
    event.dataTransfer.effectAllowed = 'move';
  }, []);

  const onDragOver = useCallback((event: React.DragEvent) => { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; }, []);

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    const type = event.dataTransfer.getData('application/reactflow');
    if (!type || !reactFlowWrapper.current) return;
    const bounds = reactFlowWrapper.current.getBoundingClientRect();
    const position = { x: event.clientX - bounds.left - 80, y: event.clientY - bounds.top - 20 };
    const newNode: WorkflowNode = {
      id: `node-${Date.now()}`, type, position,
      data: { label: getDefaultLabel(type), nodeType: type as any, step_type: getStepType(type), step_name: getDefaultLabel(type), max_rounds: 1, is_enabled: true, status: 'idle' },
    };
    addNode(newNode);
  }, [addNode]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: WorkflowNode) => { setSelectedNode(node); setShowConfig(true); }, [setSelectedNode]);
  const onPaneClick = useCallback(() => { setSelectedNode(null); setShowConfig(false); }, [setSelectedNode]);

  const handleSave = async () => { await saveWorkflow(); toast.success('工作流已保存'); };
  const handleCreate = async () => {
    if (!newWorkflowName.trim()) { toast.error('请输入工作流名称'); return; }
    const id = await createWorkflow(newWorkflowName);
    if (id) { setSearchParams({ id: String(id) }); setNewWorkflowName(''); setShowNewInput(false); toast.success('工作流已创建'); }
  };
  const handleDelete = async (id: number) => { if (confirm('确定删除此工作流？')) { await deleteWorkflow(id); setSearchParams({}); toast.success('工作流已删除'); } };
  const handleSelectWorkflow = (id: number) => { setSearchParams({ id: String(id) }); };
  const handleUpdateNode = (nodeId: string, data: Record<string, any>) => { updateNodeData(nodeId, data); setShowConfig(false); setSelectedNode(null); toast.success('节点配置已更新'); };
  const handleDeleteNode = () => { if (selectedNode) { deleteNode(selectedNode.id); setShowConfig(false); toast.success('节点已删除'); } };

  return (
    <div className="h-full flex">
      {showSidebar && (
        <div className="w-64 border-r bg-background flex flex-col">
          <div className="p-3 border-b">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium">工作流列表</h3>
              <Button variant="ghost" size="icon" onClick={() => setShowNewInput(!showNewInput)}><Plus className="h-4 w-4" /></Button>
            </div>
            {showNewInput && (
              <div className="flex gap-1">
                <Input placeholder="工作流名称" value={newWorkflowName} onChange={(e) => setNewWorkflowName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleCreate()} className="h-8 text-xs" />
                <Button size="sm" onClick={handleCreate} className="h-8 px-2">创建</Button>
              </div>
            )}
          </div>
          <ScrollArea className="flex-1">
            <div className="p-2 space-y-1">
              {workflows.map((wf) => (
                <div key={wf.id} className={`flex items-center justify-between p-2 rounded-md cursor-pointer hover:bg-accent ${currentWorkflowId === wf.id ? 'bg-accent' : ''}`} onClick={() => handleSelectWorkflow(wf.id)}>
                  <div className="flex-1 min-w-0"><div className="text-sm font-medium truncate">{wf.name}</div><div className="text-xs text-muted-foreground truncate">{wf.description}</div></div>
                  <div className="flex items-center gap-1">
                    {wf.is_default && <Badge variant="secondary" className="text-xs">默认</Badge>}
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={(e) => { e.stopPropagation(); handleDelete(wf.id); }}><Trash2 className="h-3 w-3" /></Button>
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
          <div className="p-3 border-t"><NodePalette onDragStart={onDragStart} /></div>
        </div>
      )}
      <div className="flex-1 flex flex-col">
        <div className="h-12 border-b flex items-center justify-between px-4">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={() => setShowSidebar(!showSidebar)}><List className="h-4 w-4" /></Button>
            {currentWorkflowId && <span className="text-sm text-muted-foreground">编辑中: {workflows.find((w) => w.id === currentWorkflowId)?.name || ''}</span>}
            {isDirty && <Badge variant="outline">未保存</Badge>}
          </div>
          <div className="flex items-center gap-2">
            {selectedNode && <Button variant="outline" size="sm" onClick={handleDeleteNode}><Trash2 className="h-4 w-4 mr-1" />删除节点</Button>}
            {currentWorkflowId && <Button size="sm" onClick={handleSave} disabled={!isDirty || isLoading}>{isLoading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Save className="h-4 w-4 mr-1" />}保存</Button>}
          </div>
        </div>
        <div ref={reactFlowWrapper} className="flex-1 relative" onDragOver={onDragOver} onDrop={onDrop}>
          {currentWorkflowId ? (
            <DAGCanvas nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onNodeClick={onNodeClick} onPaneClick={onPaneClick} />
          ) : (
            <div className="h-full flex items-center justify-center text-muted-foreground">
              <div className="text-center"><Settings className="h-12 w-12 mx-auto mb-4 opacity-50" /><p className="text-lg font-medium">选择或创建工作流</p><p className="text-sm">从左侧面板选择一个工作流，或创建新的工作流</p></div>
            </div>
          )}
        </div>
      </div>
      {showConfig && selectedNode && (
        <div className="w-80 border-l bg-background p-4 overflow-auto">
          <NodeConfigPanel node={selectedNode} onUpdate={handleUpdateNode} onClose={() => { setShowConfig(false); setSelectedNode(null); }} />
        </div>
      )}
    </div>
  );
}

function getDefaultLabel(type: string): string {
  const labels: Record<string, string> = { start: '开始', end: '结束', step: '处理步骤', condition: '条件判断', parallel: '并行执行', merge: '合并', agent: 'Agent调用', mcp_tool: 'MCP工具' };
  return labels[type] || '新节点';
}

function getStepType(type: string): string {
  if (type === 'start') return 'start';
  if (type === 'end') return 'end';
  if (type === 'step') return 'metadata_retrieval';
  if (type === 'agent') return 'agent_call';
  return type;
}

export default function WorkflowEditor() {
  return (
    <div className="h-full">
      <ReactFlowProvider><WorkflowEditorContent /></ReactFlowProvider>
    </div>
  );
}
