import { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import {
  Cpu, Database, Play, Trash2, CheckCircle, Clock,
  AlertTriangle, BarChart3, RefreshCcw, Zap, HardDrive,
  Upload, Download, FileText,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Spinner } from '@/components/ui/spinner';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import client from '../api/client';

interface TrainingStats {
  feedback: { positive: number; negative: number; total: number };
  training_samples: number;
  versions: { name: string; path: string; is_valid: boolean; created: string }[];
  current_model: string;
}

interface TrainingSample {
  query: string;
  positive: string;
  negative: string;
}

export default function ModelTrain() {
  const [stats, setStats] = useState<TrainingStats | null>(null);
  const [samples, setSamples] = useState<TrainingSample[]>([]);
  const [loading, setLoading] = useState(false);
  const [training, setTraining] = useState(false);
  const [activeSection, setActiveSection] = useState<'overview' | 'samples' | 'versions'>('overview');

  // Training config
  const [epochs, setEpochs] = useState(3);
  const [batchSize, setBatchSize] = useState(16);
  const [learningRate, setLearningRate] = useState('2e-5');
  const [useLora, setUseLora] = useState(true);
  const [loraRank, setLoraRank] = useState(8);
  const [loraAlpha, setLoraAlpha] = useState(16);

  // Upload state
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadStats(); }, []);

  const loadStats = async () => {
    setLoading(true);
    try {
      const { data } = await client.get('/model-train/stats');
      setStats(data);
    } catch {
      toast.error('加载统计失败');
    } finally {
      setLoading(false);
    }
  };

  const loadSamples = async () => {
    try {
      const { data } = await client.get('/model-train/all-samples');
      setSamples(data.samples);
      setActiveSection('samples');
    } catch {
      toast.error('加载样本失败');
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setUploadResult(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await client.post('/model-train/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadResult(data);
      toast.success(`上传成功: ${data.samples_generated} 条训练样本`);

      // Auto-save if samples were generated
      if (data.samples_generated > 0) {
        await client.post('/model-train/save-uploaded', data.samples);
        toast.success('训练样本已保存');
        loadStats();
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '上传失败');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const downloadTemplate = (format: 'csv' | 'json') => {
    const url = `/api/model-train/template/${format}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `training_template.${format}`;
    a.click();
  };

  const handleTrain = async () => {
    if (!stats) return;
    if (stats.training_samples < 10) {
      toast.error(`样本不足: ${stats.training_samples} 条，至少需要 10 条`);
      return;
    }
    setTraining(true);
    try {
      const { data } = await client.post('/model-train/train', {
        epochs,
        batch_size: batchSize,
        learning_rate: parseFloat(learningRate),
        use_lora: useLora,
        lora_rank: loraRank,
        lora_alpha: loraAlpha,
      });
      toast.success(data.message);
      loadStats();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '训练失败');
    } finally {
      setTraining(false);
    }
  };

  const handleLoad = async (modelPath: string) => {
    try {
      const { data } = await client.post('/model-train/load', { model_path: modelPath });
      toast.success(data.message);
      loadStats();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '加载失败');
    }
  };

  const handleDelete = async (name: string) => {
    try {
      await client.delete(`/model-train/versions/${name}`);
      toast.success('已删除');
      loadStats();
    } catch {
      toast.error('删除失败');
    }
  };

  if (loading && !stats) {
    return (
      <div className="flex items-center justify-center h-full">
        <Spinner size={32} />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b">
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-primary" />
          <h1 className="text-xl font-bold">模型微调</h1>
        </div>
        <Button variant="outline" size="sm" onClick={loadStats}>
          <RefreshCcw className="h-4 w-4 mr-1" />刷新
        </Button>
      </div>

      {/* Tabs */}
      <div className="flex border-b">
        {[
          { key: 'overview', label: '概览', icon: BarChart3 },
          { key: 'samples', label: '训练样本', icon: Database },
          { key: 'versions', label: '模型版本', icon: HardDrive },
        ].map(tab => (
          <button
            key={tab.key}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeSection === tab.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
            onClick={() => setActiveSection(tab.key as any)}
          >
            <tab.icon className="h-4 w-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <ScrollArea className="flex-1 p-4">
        {activeSection === 'overview' && stats && (
          <div className="space-y-6 max-w-3xl">
            {/* Feedback Stats */}
            <Card className="p-4">
              <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                反馈数据
              </h3>
              <div className="grid grid-cols-3 gap-4">
                <div className="text-center">
                  <p className="text-2xl font-bold text-primary">{stats.feedback.total}</p>
                  <p className="text-xs text-muted-foreground">总反馈</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-green-600">{stats.feedback.positive}</p>
                  <p className="text-xs text-muted-foreground">👍 满意</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold text-red-600">{stats.feedback.negative}</p>
                  <p className="text-xs text-muted-foreground">👎 不满意</p>
                </div>
              </div>
              <div className="mt-3">
                <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                  <span>满意度</span>
                  <span>{stats.feedback.total > 0 ? Math.round(stats.feedback.positive / stats.feedback.total * 100) : 0}%</span>
                </div>
                <div className="w-full bg-muted rounded-full h-2">
                  <div
                    className="bg-green-500 h-2 rounded-full transition-all"
                    style={{ width: `${stats.feedback.total > 0 ? (stats.feedback.positive / stats.feedback.total * 100) : 0}%` }}
                  />
                </div>
              </div>
            </Card>

            {/* Training Samples */}
            <Card className="p-4">
              <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                <Database className="h-4 w-4" />
                训练样本
              </h3>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-lg font-bold">{stats.training_samples}</p>
                  <p className="text-xs text-muted-foreground">可用训练三元组 (query, positive, negative)</p>
                </div>
                <Button variant="outline" size="sm" onClick={loadSamples}>
                  查看样本
                </Button>
              </div>
              {stats.training_samples < 10 && (
                <div className="mt-3 p-2 bg-yellow-500/10 rounded-md flex items-center gap-2 text-sm text-yellow-600">
                  <AlertTriangle className="h-4 w-4" />
                  样本不足，建议积累到 100+ 条反馈后再训练
                </div>
              )}

              {/* Upload & Download */}
              <div className="mt-4 pt-4 border-t">
                <p className="text-xs font-medium text-muted-foreground mb-2">导入训练数据</p>
                <div className="flex items-center gap-2 flex-wrap">
                  <input ref={fileRef} type="file" accept=".csv,.json" onChange={handleUpload} className="hidden" />
                  <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()} disabled={uploading}>
                    {uploading ? <Spinner className="mr-1" size={14} /> : <Upload className="h-3.5 w-3.5 mr-1" />}
                    上传 CSV/JSON
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => downloadTemplate('csv')}>
                    <Download className="h-3.5 w-3.5 mr-1" />CSV 模板
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => downloadTemplate('json')}>
                    <Download className="h-3.5 w-3.5 mr-1" />JSON 模板
                  </Button>
                </div>
                {uploadResult && (
                  <div className="mt-2 p-2 bg-green-500/10 rounded-md text-xs text-green-600">
                    成功导入 {uploadResult.samples_generated} 条样本
                    {uploadResult.skipped_tables?.length > 0 && (
                      <span className="text-yellow-600">，跳过 {uploadResult.skipped_tables.length} 个未找到的表</span>
                    )}
                  </div>
                )}
              </div>
            </Card>

            {/* Current Model */}
            <Card className="p-4">
              <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                <Cpu className="h-4 w-4" />
                当前模型
              </h3>
              <p className="font-mono text-sm">{stats.current_model}</p>
            </Card>

            {/* Training Config */}
            <Card className="p-4">
              <h3 className="text-sm font-medium mb-4 flex items-center gap-2">
                <Zap className="h-4 w-4" />
                微调配置
              </h3>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>训练轮数</Label>
                  <Input type="number" min={1} max={20} value={epochs}
                    onChange={(e) => setEpochs(parseInt(e.target.value) || 3)} />
                </div>
                <div className="space-y-2">
                  <Label>Batch Size</Label>
                  <Input type="number" min={1} max={128} value={batchSize}
                    onChange={(e) => setBatchSize(parseInt(e.target.value) || 16)} />
                </div>
                <div className="space-y-2">
                  <Label>学习率</Label>
                  <Input value={learningRate}
                    onChange={(e) => setLearningRate(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Label>LoRA 微调</Label>
                    <Switch checked={useLora} onCheckedChange={setUseLora} />
                  </div>
                  <p className="text-xs text-muted-foreground">数据量少时推荐开启，防止过拟合</p>
                </div>
                {useLora && (
                  <>
                    <div className="space-y-2">
                      <Label>LoRA Rank</Label>
                      <Input type="number" min={1} max={64} value={loraRank}
                        onChange={(e) => setLoraRank(parseInt(e.target.value) || 8)} />
                    </div>
                    <div className="space-y-2">
                      <Label>LoRA Alpha</Label>
                      <Input type="number" min={1} max={128} value={loraAlpha}
                        onChange={(e) => setLoraAlpha(parseInt(e.target.value) || 16)} />
                    </div>
                  </>
                )}
              </div>
              <Separator className="my-4" />
              <Button onClick={handleTrain} disabled={training || stats.training_samples < 10}>
                {training ? <Spinner className="mr-2" size={16} /> : <Play className="h-4 w-4 mr-2" />}
                {training ? '训练中...' : '开始训练'}
              </Button>
            </Card>

            {/* Model Versions */}
            <Card className="p-4">
              <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                <HardDrive className="h-4 w-4" />
                模型版本 ({stats.versions.length})
              </h3>
              {stats.versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">暂无微调模型</p>
              ) : (
                <div className="space-y-2">
                  {stats.versions.map(v => (
                    <div key={v.name} className="flex items-center justify-between p-2 rounded-md border">
                      <div>
                        <p className="text-sm font-mono">{v.name}</p>
                        <p className="text-xs text-muted-foreground">{v.created}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        {v.is_valid ? (
                          <Badge variant="outline" className="text-green-600">有效</Badge>
                        ) : (
                          <Badge variant="outline" className="text-red-600">无效</Badge>
                        )}
                        <Button size="sm" variant="outline" onClick={() => handleLoad(v.path)}>
                          加载
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(v.name)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        )}

        {activeSection === 'samples' && (
          <div className="space-y-4 max-w-4xl">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">训练样本预览 (共 {samples.length} 条)</h3>
              <Button variant="outline" size="sm" onClick={loadSamples}>
                <RefreshCcw className="h-4 w-4 mr-1" />刷新
              </Button>
            </div>
            {samples.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Database className="h-12 w-12 mx-auto mb-2 opacity-50" />
                <p>暂无训练样本</p>
                <p className="text-xs mt-1">用户在 Chat 页面反馈后自动生成</p>
              </div>
            ) : (
              <div className="space-y-2">
                {samples.map((s, i) => (
                  <Card key={i} className="p-3 text-xs">
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <span className="text-muted-foreground">Query:</span>
                        <p className="font-mono mt-1">{s.query}</p>
                      </div>
                      <div>
                        <span className="text-green-600">Positive:</span>
                        <p className="font-mono mt-1">{s.positive}</p>
                      </div>
                      <div>
                        <span className="text-red-600">Negative:</span>
                        <p className="font-mono mt-1">{s.negative}</p>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}

        {activeSection === 'versions' && stats && (
          <div className="space-y-4 max-w-3xl">
            <h3 className="text-sm font-medium">模型版本管理</h3>
            {stats.versions.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <HardDrive className="h-12 w-12 mx-auto mb-2 opacity-50" />
                <p>暂无微调模型</p>
                <p className="text-xs mt-1">训练完成后会出现在这里</p>
              </div>
            ) : (
              <div className="space-y-3">
                {stats.versions.map(v => (
                  <Card key={v.name} className="p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-mono font-medium">{v.name}</p>
                        <p className="text-xs text-muted-foreground mt-1">创建时间: {v.created}</p>
                        <p className="text-xs text-muted-foreground font-mono">{v.path}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        {v.is_valid ? (
                          <Badge variant="outline" className="text-green-600">有效</Badge>
                        ) : (
                          <Badge variant="outline" className="text-red-600">无效</Badge>
                        )}
                        <Button size="sm" onClick={() => handleLoad(v.path)}>
                          <CheckCircle className="h-3.5 w-3.5 mr-1" />加载
                        </Button>
                        <Button size="sm" variant="destructive" onClick={() => handleDelete(v.name)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
