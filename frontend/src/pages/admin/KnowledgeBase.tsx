import React, { useState, useEffect, useRef } from 'react';
import client from '../../api/client';

interface Document {
  id: string;
  title: string;
  doc_type: string;
  source: string;
  size: string;
  file_path?: string;
  created_at: string;
  updated_at: string;
  tags: string[];
}

interface KnowledgeStatus {
  document_count: number;
  chunk_count: number;
  vector_count: number;
  last_sync: string | null;
  sync_status: string;
}

interface GraphNode {
  id: number;
  labels: string[];
  properties: Record<string, any>;
}

interface GraphRelationship {
  id: number;
  type: string;
  source: number;
  target: number;
  properties: Record<string, any>;
}

const KnowledgeBase: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'status' | 'documents' | 'upload' | 'sync' | 'graph'>('status');
  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 文档查看状态
  const [viewingDoc, setViewingDoc] = useState<{ id: string; title: string; content: string } | null>(null);

  // 知识图谱状态
  const [graphData, setGraphData] = useState<{
    stats: any;
    nodes: GraphNode[];
    relationships: GraphRelationship[];
  } | null>(null);

  useEffect(() => {
    loadStatus();
    loadDocuments();
  }, []);

  const loadStatus = async () => {
    try {
      const response = await client.get('/ai-assistant/knowledge/status');
      setStatus(response.data);
    } catch (error) {
      console.error('Load status error:', error);
    }
  };

  const loadDocuments = async () => {
    try {
      setIsLoading(true);
      const response = await client.get('/ai-assistant/knowledge/documents');
      setDocuments(response.data.documents || []);
    } catch (error) {
      console.error('Load documents error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const loadGraphData = async () => {
    try {
      setIsLoading(true);
      const response = await client.get('/ai-assistant/knowledge/graph');
      setGraphData(response.data);
    } catch (error) {
      console.error('Load graph data error:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDebug = async () => {
    try {
      const response = await client.get('/ai-assistant/knowledge/debug');
      const debugInfo = response.data;

      console.log('Debug info:', debugInfo);

      // 显示调试信息
      let message = '知识库调试信息：\n\n';

      message += '表状态：\n';
      for (const [table, info] of Object.entries(debugInfo.tables)) {
        const tableInfo = info as any;
        if (tableInfo.exists) {
          message += `  ✓ ${table}: ${tableInfo.count} 条记录\n`;
        } else {
          message += `  ✗ ${table}: 不存在\n`;
        }
      }

      message += '\n数据统计：\n';
      message += `  活跃表数量: ${debugInfo.counts.active_tables || 0}\n`;
      message += `  活跃字段数量: ${debugInfo.counts.active_columns || 0}\n`;
      message += `  知识库文档: ${debugInfo.counts.knowledge_docs || 0}\n`;
      message += `  知识库分块: ${debugInfo.counts.knowledge_chunks || 0}\n`;

      if (debugInfo.errors.length > 0) {
        message += '\n错误：\n';
        debugInfo.errors.forEach((err: string) => {
          message += `  - ${err}\n`;
        });
      }

      alert(message);
    } catch (error) {
      console.error('Debug error:', error);
      alert('调试失败，请查看控制台日志');
    }
  };

  const handleSync = async (source: string) => {
    try {
      setIsLoading(true);
      await client.post('/ai-assistant/knowledge/update', { source, force: false });
      await loadStatus();
      await loadDocuments();
      alert('同步完成！');
    } catch (error) {
      console.error('Sync error:', error);
      alert('同步失败，请重试');
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    setUploadProgress('正在上传...');
    setUploadError(null);

    try {
      const formData = new FormData();
      Array.from(files).forEach(file => {
        formData.append('files', file);
      });
      formData.append('doc_type', 'guide');

      const response = await client.post('/ai-assistant/knowledge/upload-multiple', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      setUploadProgress(`上传完成：${response.data.uploaded} 个成功，${response.data.failed} 个失败`);

      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }

      await loadStatus();
      await loadDocuments();

      setTimeout(() => setUploadProgress(null), 3000);
    } catch (error: any) {
      setUploadError(error.message || '上传失败');
    }
  };

  const handleDelete = async (docId: string) => {
    if (deleteConfirm === docId) {
      try {
        await client.delete(`/ai-assistant/knowledge/documents/${docId}`);
        setDeleteConfirm(null);
        await loadStatus();
        await loadDocuments();
      } catch (error) {
        console.error('Delete error:', error);
        alert('删除失败');
      }
    } else {
      setDeleteConfirm(docId);
      setTimeout(() => setDeleteConfirm(null), 3000);
    }
  };

  const handleViewDocument = async (docId: string) => {
    try {
      const response = await client.get(`/ai-assistant/knowledge/documents/${docId}/content`);
      setViewingDoc(response.data);
    } catch (error) {
      console.error('View document error:', error);
      alert('获取文档内容失败');
    }
  };

  const handleCloseViewer = () => {
    setViewingDoc(null);
  };

  // 渲染Markdown内容（简单渲染）
  const renderMarkdown = (content: string) => {
    // 简单的Markdown渲染
    return content
      .replace(/^### (.*$)/gm, '<h3 class="text-lg font-semibold mt-4 mb-2">$1</h3>')
      .replace(/^## (.*$)/gm, '<h2 class="text-xl font-bold mt-6 mb-3">$1</h2>')
      .replace(/^# (.*$)/gm, '<h1 class="text-2xl font-bold mt-8 mb-4">$1</h1>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/`(.*?)`/g, '<code class="bg-gray-100 px-1 rounded">$1</code>')
      .replace(/^- (.*$)/gm, '<li class="ml-4">$1</li>')
      .replace(/^\d+\. (.*$)/gm, '<li class="ml-4 list-decimal">$1</li>')
      .replace(/\n\n/g, '</p><p class="mb-4">')
      .replace(/\n/g, '<br/>');
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">知识库管理</h1>
        <p className="text-gray-600 mt-1">管理AI助手的知识库，包括文档上传、同步和查询</p>
      </div>

      {/* Tab导航 */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="-mb-px flex space-x-8">
          <button
            onClick={() => setActiveTab('status')}
            className={`py-4 px-1 border-b-2 font-medium text-sm ${
              activeTab === 'status'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            📊 状态概览
          </button>
          <button
            onClick={() => setActiveTab('documents')}
            className={`py-4 px-1 border-b-2 font-medium text-sm ${
              activeTab === 'documents'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            📄 文档管理
          </button>
          <button
            onClick={() => setActiveTab('upload')}
            className={`py-4 px-1 border-b-2 font-medium text-sm ${
              activeTab === 'upload'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            📤 上传文档
          </button>
          <button
            onClick={() => setActiveTab('sync')}
            className={`py-4 px-1 border-b-2 font-medium text-sm ${
              activeTab === 'sync'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            🔄 知识库同步
          </button>
          <button
            onClick={() => {
              setActiveTab('graph');
              if (!graphData) loadGraphData();
            }}
            className={`py-4 px-1 border-b-2 font-medium text-sm ${
              activeTab === 'graph'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            🔗 知识图谱
          </button>
        </nav>
      </div>

      {/* 状态概览 */}
      {activeTab === 'status' && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">文档数量</div>
              <div className="mt-2 text-3xl font-bold text-blue-600">
                {status?.document_count || 0}
              </div>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">分块数量</div>
              <div className="mt-2 text-3xl font-bold text-green-600">
                {status?.chunk_count || 0}
              </div>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">向量数量</div>
              <div className="mt-2 text-3xl font-bold text-purple-600">
                {status?.vector_count || 0}
              </div>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">同步状态</div>
              <div className="mt-2 text-3xl font-bold text-orange-600">
                {status?.sync_status === 'idle' ? '空闲' : '同步中'}
              </div>
              {status?.last_sync && (
                <div className="mt-2 text-xs text-gray-500">
                  最后同步：{new Date(status.last_sync).toLocaleString('zh-CN')}
                </div>
              )}
            </div>
          </div>

          {/* 调试按钮 */}
          <div className="bg-white rounded-lg shadow p-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-medium text-gray-900">诊断工具</h3>
                <p className="text-sm text-gray-500 mt-1">
                  如果同步后数据仍然显示为0，点击此按钮检查数据库状态
                </p>
              </div>
              <button
                onClick={handleDebug}
                className="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600 transition-colors"
              >
                检查数据库状态
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 文档管理 */}
      {activeTab === 'documents' && (
        <div className="bg-white rounded-lg shadow">
          <div className="px-6 py-4 border-b border-gray-200">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-medium text-gray-900">文档列表</h3>
              <button
                onClick={loadDocuments}
                className="text-sm text-blue-500 hover:text-blue-700"
              >
                刷新
              </button>
            </div>
          </div>
          <div className="divide-y divide-gray-200">
            {isLoading ? (
              <div className="p-6 text-center">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
              </div>
            ) : documents.length === 0 ? (
              <div className="p-6 text-center text-gray-500">
                暂无文档，请上传或同步知识库
              </div>
            ) : (
              documents.map((doc) => (
                <div key={doc.id} className="px-6 py-4 hover:bg-gray-50">
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-2">
                        <h4 className="text-sm font-medium text-gray-900 truncate">{doc.title}</h4>
                        <span className="px-2 py-0.5 text-xs bg-blue-100 text-blue-800 rounded">
                          {doc.doc_type}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        {doc.source} • {doc.size} • 更新于 {new Date(doc.updated_at).toLocaleString('zh-CN')}
                      </div>
                      {doc.file_path && (
                        <div className="mt-1 text-xs text-gray-400">
                          路径: {doc.file_path}
                        </div>
                      )}
                      {doc.tags.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {doc.tags.map((tag, idx) => (
                            <span key={idx} className="px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center space-x-2 ml-4">
                      <button
                        onClick={() => handleViewDocument(doc.id)}
                        className="px-3 py-1 text-sm text-blue-500 hover:bg-blue-50 rounded"
                      >
                        查看
                      </button>
                      <button
                        onClick={() => handleDelete(doc.id)}
                        className={`px-3 py-1 text-sm rounded ${
                          deleteConfirm === doc.id
                            ? 'bg-red-500 text-white'
                            : 'text-red-500 hover:bg-red-50'
                        }`}
                      >
                        {deleteConfirm === doc.id ? '确认删除' : '删除'}
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* 上传文档 */}
      {activeTab === 'upload' && (
        <div className="bg-white rounded-lg shadow p-6">
          <h3 className="text-lg font-medium text-gray-900 mb-4">上传文档</h3>

          <div className="mb-4 p-4 bg-blue-50 rounded-lg">
            <h4 className="text-sm font-medium text-blue-800 mb-2">支持的文件类型</h4>
            <p className="text-sm text-blue-700">.md, .txt, .rst, .json, .yaml, .yml</p>
            <p className="text-sm text-blue-700 mt-1">单个文件最大 10MB，最多同时上传 10 个文件</p>
          </div>

          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".md,.txt,.rst,.json,.yaml,.yml"
              onChange={handleFileUpload}
              className="hidden"
              id="file-upload"
            />
            <label htmlFor="file-upload" className="cursor-pointer">
              <div className="text-4xl mb-4">📁</div>
              <div className="text-gray-600">
                <p className="font-medium">点击选择文件</p>
                <p className="text-sm mt-1">或拖拽文件到此处</p>
              </div>
            </label>
          </div>

          {uploadProgress && (
            <div className="mt-4 p-3 bg-green-50 rounded-lg">
              <div className="flex items-center">
                <span className="text-green-500 mr-2">✅</span>
                <span className="text-sm text-green-700">{uploadProgress}</span>
              </div>
            </div>
          )}

          {uploadError && (
            <div className="mt-4 p-3 bg-red-50 rounded-lg">
              <div className="flex items-center">
                <span className="text-red-500 mr-2">❌</span>
                <span className="text-sm text-red-700">{uploadError}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 知识库同步 */}
      {activeTab === 'sync' && (
        <div className="space-y-6">
          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-medium text-gray-900 mb-4">知识库同步</h3>
            <p className="text-gray-600 mb-6">
              同步知识库会重新处理所有文档，生成向量索引。此过程可能需要几分钟时间。
            </p>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <button
                onClick={() => handleSync('docs')}
                disabled={isLoading}
                className="p-4 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 transition-colors"
              >
                <div className="text-2xl mb-2">📚</div>
                <div className="font-medium">同步项目文档</div>
                <div className="text-sm text-blue-100 mt-1">同步 /docs 目录</div>
              </button>

              <button
                onClick={() => handleSync('database')}
                disabled={isLoading}
                className="p-4 bg-green-500 text-white rounded-lg hover:bg-green-600 disabled:opacity-50 transition-colors"
              >
                <div className="text-2xl mb-2">🗄️</div>
                <div className="font-medium">同步数据库元数据</div>
                <div className="text-sm text-green-100 mt-1">同步表结构和字段</div>
              </button>

              <button
                onClick={() => handleSync('all')}
                disabled={isLoading}
                className="p-4 bg-purple-500 text-white rounded-lg hover:bg-purple-600 disabled:opacity-50 transition-colors"
              >
                <div className="text-2xl mb-2">🔄</div>
                <div className="font-medium">全量同步</div>
                <div className="text-sm text-purple-100 mt-1">同步所有来源</div>
              </button>
            </div>

            {isLoading && (
              <div className="mt-4 flex items-center justify-center">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500 mr-2"></div>
                <span className="text-gray-600">同步中...</span>
              </div>
            )}
          </div>

          <div className="bg-white rounded-lg shadow p-6">
            <h3 className="text-lg font-medium text-gray-900 mb-4">同步说明</h3>
            <ul className="space-y-2 text-gray-600">
              <li className="flex items-start">
                <span className="mr-2">•</span>
                <span><strong>项目文档</strong>：同步项目 /docs 目录下的所有文档文件</span>
              </li>
              <li className="flex items-start">
                <span className="mr-2">•</span>
                <span><strong>数据库元数据</strong>：同步表结构、字段说明、业务术语等元数据</span>
              </li>
              <li className="flex items-start">
                <span className="mr-2">•</span>
                <span><strong>全量同步</strong>：同时同步文档和数据库元数据</span>
              </li>
              <li className="flex items-start">
                <span className="mr-2">•</span>
                <span>同步完成后，AI助手将能够使用最新的知识库内容回答问题</span>
              </li>
            </ul>
          </div>
        </div>
      )}

      {/* 知识图谱 */}
      {activeTab === 'graph' && (
        <div className="space-y-6">
          {/* 图谱统计 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">节点数量</div>
              <div className="mt-2 text-3xl font-bold text-blue-600">
                {graphData?.stats?.node_count || 0}
              </div>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">关系数量</div>
              <div className="mt-2 text-3xl font-bold text-green-600">
                {graphData?.stats?.relationship_count || 0}
              </div>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <div className="text-sm font-medium text-gray-500">连接状态</div>
              <div className="mt-2 text-3xl font-bold text-purple-600">
                {graphData?.stats?.connected ? '已连接' : '未连接'}
              </div>
            </div>
          </div>

          {/* 节点类型统计 */}
          {graphData?.stats?.labels && graphData.stats.labels.length > 0 && (
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4">节点类型</h3>
              <div className="flex flex-wrap gap-2">
                {graphData.stats.labels.map((label: string, idx: number) => (
                  <span key={idx} className="px-3 py-1 bg-blue-100 text-blue-800 rounded-full text-sm">
                    {label}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 节点列表 */}
          {graphData?.nodes && graphData.nodes.length > 0 && (
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4">节点列表</h3>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        ID
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        类型
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        名称
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        属性
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {graphData.nodes.slice(0, 50).map((node) => (
                      <tr key={node.id}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {node.id}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className="px-2 py-1 text-xs bg-blue-100 text-blue-800 rounded">
                            {node.labels.join(', ')}
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                          {node.properties.name || node.properties.title || '-'}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 max-w-xs truncate">
                          {Object.entries(node.properties)
                            .filter(([key]) => !['id', 'name', 'title'].includes(key))
                            .slice(0, 3)
                            .map(([key, value]) => `${key}: ${value}`)
                            .join(', ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {graphData.nodes.length > 50 && (
                <div className="mt-4 text-center text-sm text-gray-500">
                  显示前 50 个节点，共 {graphData.nodes.length} 个
                </div>
              )}
            </div>
          )}

          {/* 关系列表 */}
          {graphData?.relationships && graphData.relationships.length > 0 && (
            <div className="bg-white rounded-lg shadow p-6">
              <h3 className="text-lg font-medium text-gray-900 mb-4">关系列表</h3>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        ID
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        类型
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        源节点
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        目标节点
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-gray-200">
                    {graphData.relationships.slice(0, 50).map((rel) => (
                      <tr key={rel.id}>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                          {rel.id}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap">
                          <span className="px-2 py-1 text-xs bg-green-100 text-green-800 rounded">
                            {rel.type}
                          </span>
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {rel.source}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                          {rel.target}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {graphData.relationships.length > 50 && (
                <div className="mt-4 text-center text-sm text-gray-500">
                  显示前 50 个关系，共 {graphData.relationships.length} 个
                </div>
              )}
            </div>
          )}

          {/* 无数据提示 */}
          {(!graphData || (graphData.nodes.length === 0 && graphData.relationships.length === 0)) && (
            <div className="bg-white rounded-lg shadow p-6 text-center text-gray-500">
              <div className="text-4xl mb-4">🔗</div>
              <p>暂无知识图谱数据</p>
              <p className="text-sm mt-2">请先同步数据库元数据以生成知识图谱</p>
            </div>
          )}
        </div>
      )}

      {/* 文档查看弹窗 */}
      {viewingDoc && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <h3 className="text-lg font-medium text-gray-900">{viewingDoc.title}</h3>
              <button
                onClick={handleCloseViewer}
                className="text-gray-400 hover:text-gray-600"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              <div
                className="prose max-w-none"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(viewingDoc.content) }}
              />
            </div>
            <div className="px-6 py-4 border-t border-gray-200 bg-gray-50">
              <button
                onClick={handleCloseViewer}
                className="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600"
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default KnowledgeBase;
