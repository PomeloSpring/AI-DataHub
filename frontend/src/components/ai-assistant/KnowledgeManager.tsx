import React, { useState, useEffect, useRef } from 'react';
import { useAIAssistantStore } from '../../stores/aiAssistantStore';

interface Document {
  id: string;
  title: string;
  doc_type: string;
  source: string;
  size: string;
  created_at: string;
  updated_at: string;
  tags: string[];
}

const KnowledgeManager: React.FC = () => {
  const {
    knowledgeStatus,
    documents,
    isLoading,
    loadKnowledgeStatus,
    loadDocuments,
    updateKnowledge,
    deleteDocument,
    uploadDocument,
    uploadMultipleDocuments
  } = useAIAssistantStore();

  const [activeTab, setActiveTab] = useState<'status' | 'documents' | 'sync' | 'upload'>('status');
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadKnowledgeStatus();
    loadDocuments();
  }, []);

  // 同步知识库
  const handleSync = async (source: string) => {
    await updateKnowledge(source, false);
    await loadKnowledgeStatus();
  };

  // 删除文档
  const handleDelete = async (docId: string) => {
    if (deleteConfirm === docId) {
      await deleteDocument(docId);
      setDeleteConfirm(null);
      await loadDocuments();
    } else {
      setDeleteConfirm(docId);
      // 3秒后自动取消确认
      setTimeout(() => setDeleteConfirm(null), 3000);
    }
  };

  // 处理文件上传
  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    setUploadProgress('正在上传...');
    setUploadError(null);

    try {
      if (files.length === 1) {
        // 单文件上传
        await uploadDocument(files[0]);
        setUploadProgress('上传成功！');
      } else {
        // 批量上传
        const fileArray = Array.from(files);
        const result = await uploadMultipleDocuments(fileArray);
        setUploadProgress(`上传完成：${result.uploaded} 个成功，${result.failed} 个失败`);
      }

      // 清空文件输入
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }

      // 3秒后清除提示
      setTimeout(() => setUploadProgress(null), 3000);

    } catch (error: any) {
      setUploadError(error.message || '上传失败');
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Tab导航 */}
      <div className="flex border-b border-gray-200 bg-gray-50">
        <button
          onClick={() => setActiveTab('status')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors
            ${activeTab === 'status'
              ? 'text-blue-600 border-b-2 border-blue-600 bg-white'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
            }`}
        >
          📊 状态
        </button>
        <button
          onClick={() => setActiveTab('documents')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors
            ${activeTab === 'documents'
              ? 'text-blue-600 border-b-2 border-blue-600 bg-white'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
            }`}
        >
          📄 文档
        </button>
        <button
          onClick={() => setActiveTab('upload')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors
            ${activeTab === 'upload'
              ? 'text-blue-600 border-b-2 border-blue-600 bg-white'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
            }`}
        >
          📤 上传
        </button>
        <button
          onClick={() => setActiveTab('sync')}
          className={`flex-1 px-4 py-3 text-sm font-medium transition-colors
            ${activeTab === 'sync'
              ? 'text-blue-600 border-b-2 border-blue-600 bg-white'
              : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
            }`}
        >
          🔄 同步
        </button>
      </div>

      {/* 内容区域 */}
      <div className="flex-1 overflow-y-auto">
        {/* 状态Tab */}
        {activeTab === 'status' && (
          <div className="p-4 space-y-4">
            <h4 className="font-semibold text-gray-800">知识库状态</h4>

            {/* 统计卡片 */}
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-blue-50 rounded-lg p-3">
                <div className="text-2xl font-bold text-blue-600">
                  {knowledgeStatus?.document_count || 0}
                </div>
                <div className="text-xs text-blue-500">文档数量</div>
              </div>
              <div className="bg-green-50 rounded-lg p-3">
                <div className="text-2xl font-bold text-green-600">
                  {knowledgeStatus?.chunk_count || 0}
                </div>
                <div className="text-xs text-green-500">分块数量</div>
              </div>
              <div className="bg-purple-50 rounded-lg p-3">
                <div className="text-2xl font-bold text-purple-600">
                  {knowledgeStatus?.vector_count || 0}
                </div>
                <div className="text-xs text-purple-500">向量数量</div>
              </div>
              <div className="bg-orange-50 rounded-lg p-3">
                <div className="text-2xl font-bold text-orange-600">
                  {knowledgeStatus?.sync_status === 'idle' ? '空闲' : '同步中'}
                </div>
                <div className="text-xs text-orange-500">同步状态</div>
              </div>
            </div>

            {/* 最后同步时间 */}
            {knowledgeStatus?.last_sync && (
              <div className="bg-gray-50 rounded-lg p-3">
                <div className="text-sm text-gray-600">
                  最后同步：{new Date(knowledgeStatus.last_sync).toLocaleString('zh-CN')}
                </div>
              </div>
            )}
          </div>
        )}

        {/* 文档Tab */}
        {activeTab === 'documents' && (
          <div className="p-4">
            <div className="flex items-center justify-between mb-4">
              <h4 className="font-semibold text-gray-800">文档列表</h4>
              <button
                onClick={() => loadDocuments()}
                className="text-sm text-blue-500 hover:text-blue-700"
              >
                刷新
              </button>
            </div>

            {documents.length === 0 ? (
              <div className="text-center text-gray-500 py-8">
                <div className="text-4xl mb-2">📭</div>
                <p>暂无文档</p>
                <p className="text-sm mt-1">点击下方按钮同步知识库</p>
              </div>
            ) : (
              <div className="space-y-3">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="bg-gray-50 rounded-lg p-3 hover:bg-gray-100 transition-colors"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="font-medium text-gray-800">{doc.title}</div>
                        <div className="text-xs text-gray-500 mt-1">
                          {doc.doc_type} • {doc.size}
                        </div>
                        {doc.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {doc.tags.map((tag, idx) => (
                              <span
                                key={idx}
                                className="text-xs px-2 py-0.5 bg-blue-100 text-blue-600 rounded"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => handleDelete(doc.id)}
                        className={`text-sm px-2 py-1 rounded transition-colors ${
                          deleteConfirm === doc.id
                            ? 'bg-red-500 text-white'
                            : 'text-red-500 hover:bg-red-50'
                        }`}
                      >
                        {deleteConfirm === doc.id ? '确认删除' : '删除'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 上传Tab */}
        {activeTab === 'upload' && (
          <div className="p-4 space-y-4">
            <h4 className="font-semibold text-gray-800">上传文档</h4>

            {/* 上传提示 */}
            <div className="bg-blue-50 rounded-lg p-3">
              <div className="flex items-start space-x-2">
                <span className="text-blue-500">💡</span>
                <div className="text-sm text-blue-700">
                  <p className="font-medium mb-1">支持的文件类型</p>
                  <p>.md, .txt, .rst, .json, .yaml, .yml</p>
                  <p className="mt-1">单个文件最大 10MB，最多同时上传 10 个文件</p>
                </div>
              </div>
            </div>

            {/* 文件上传区域 */}
            <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center hover:border-blue-400 transition-colors">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".md,.txt,.rst,.json,.yaml,.yml"
                onChange={handleFileUpload}
                className="hidden"
                id="file-upload"
              />
              <label
                htmlFor="file-upload"
                className="cursor-pointer"
              >
                <div className="text-4xl mb-2">📁</div>
                <div className="text-gray-600">
                  <p className="font-medium">点击选择文件</p>
                  <p className="text-sm">或拖拽文件到此处</p>
                </div>
              </label>
            </div>

            {/* 上传进度 */}
            {uploadProgress && (
              <div className="bg-green-50 rounded-lg p-3">
                <div className="flex items-center space-x-2">
                  <span className="text-green-500">✅</span>
                  <span className="text-sm text-green-700">{uploadProgress}</span>
                </div>
              </div>
            )}

            {/* 上传错误 */}
            {uploadError && (
              <div className="bg-red-50 rounded-lg p-3">
                <div className="flex items-center space-x-2">
                  <span className="text-red-500">❌</span>
                  <span className="text-sm text-red-700">{uploadError}</span>
                </div>
              </div>
            )}

            {/* 上传说明 */}
            <div className="bg-gray-50 rounded-lg p-3">
              <h5 className="text-sm font-medium text-gray-700 mb-2">上传说明</h5>
              <ul className="text-xs text-gray-600 space-y-1">
                <li>• 文档会自动分块并向量化</li>
                <li>• 支持 Markdown 格式的文档</li>
                <li>• 上传后可用于 AI 助手的知识库检索</li>
                <li>• 建议使用清晰的标题和结构</li>
              </ul>
            </div>
          </div>
        )}

        {/* 同步Tab */}
        {activeTab === 'sync' && (
          <div className="p-4 space-y-4">
            <h4 className="font-semibold text-gray-800">知识库同步</h4>

            <div className="bg-yellow-50 rounded-lg p-3">
              <div className="flex items-start space-x-2">
                <span className="text-yellow-500">⚠️</span>
                <div className="text-sm text-yellow-700">
                  <p className="font-medium">注意</p>
                  <p>同步知识库会重新处理所有文档，可能需要几分钟时间。</p>
                </div>
              </div>
            </div>

            {/* 同步按钮 */}
            <div className="space-y-3">
              <button
                onClick={() => handleSync('docs')}
                disabled={isLoading}
                className="w-full px-4 py-3 bg-blue-500 text-white rounded-lg
                         hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed
                         transition-colors flex items-center justify-center space-x-2"
              >
                {isLoading ? (
                  <>
                    <span className="animate-spin">⏳</span>
                    <span>同步中...</span>
                  </>
                ) : (
                  <>
                    <span>📚</span>
                    <span>同步项目文档</span>
                  </>
                )}
              </button>

              <button
                onClick={() => handleSync('database')}
                disabled={isLoading}
                className="w-full px-4 py-3 bg-green-500 text-white rounded-lg
                         hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed
                         transition-colors flex items-center justify-center space-x-2"
              >
                {isLoading ? (
                  <>
                    <span className="animate-spin">⏳</span>
                    <span>同步中...</span>
                  </>
                ) : (
                  <>
                    <span>🗄️</span>
                    <span>同步数据库元数据</span>
                  </>
                )}
              </button>

              <button
                onClick={() => handleSync('all')}
                disabled={isLoading}
                className="w-full px-4 py-3 bg-purple-500 text-white rounded-lg
                         hover:bg-purple-600 disabled:opacity-50 disabled:cursor-not-allowed
                         transition-colors flex items-center justify-center space-x-2"
              >
                {isLoading ? (
                  <>
                    <span className="animate-spin">⏳</span>
                    <span>同步中...</span>
                  </>
                ) : (
                  <>
                    <span>🔄</span>
                    <span>全量同步</span>
                  </>
                )}
              </button>
            </div>

            {/* 同步说明 */}
            <div className="bg-gray-50 rounded-lg p-3">
              <h5 className="text-sm font-medium text-gray-700 mb-2">同步说明</h5>
              <ul className="text-xs text-gray-600 space-y-1">
                <li>• <strong>项目文档</strong>：同步 /docs 目录下的文档</li>
                <li>• <strong>数据库元数据</strong>：同步表结构、字段说明等</li>
                <li>• <strong>全量同步</strong>：同步所有知识来源</li>
              </ul>
            </div>
          </div>
        )}
      </div>

      {/* 底部操作栏 */}
      {activeTab === 'status' && (
        <div className="p-4 border-t border-gray-200">
          <button
            onClick={() => handleSync('all')}
            disabled={isLoading}
            className="w-full px-4 py-2 bg-green-500 text-white rounded-lg
                     hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors"
          >
            {isLoading ? '同步中...' : '立即同步知识库'}
          </button>
        </div>
      )}
    </div>
  );
};

export default KnowledgeManager;
