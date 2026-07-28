import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  Search, Download, Check, ExternalLink, Star, Package, Loader2,
  Database, FolderOpen, Wrench, Cloud, MessageSquare, Image, Brain, Globe,
} from 'lucide-react';
import client from '@/api/client';
import { McpInstallProgress } from '@/components/McpInstallProgress';

// ── Types ─────────────────────────────────────────────────────────

interface MCPRegistryItem {
  id: number;
  name: string;
  package_name: string;
  description: string;
  author: string;
  homepage: string;
  install_type: string;
  default_args: string;
  required_env: string;
  category: string;
  tags: string;
  stars: number;
  is_verified: number;
  is_popular: number;
  is_installed?: boolean;
}

interface Category {
  key: string;
  label: string;
  icon: string;
  count: number;
}

interface NpmResult {
  name: string;
  version: string;
  description: string;
  author: string;
  homepage: string;
  npm_url: string;
  keywords: string[];
}

// ── Icon map ──────────────────────────────────────────────────────

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  Database, FolderOpen, Wrench, Cloud, MessageSquare, Image, Brain, Package,
  Search: Globe,
};

function getCategoryIcon(icon?: string) {
  return ICON_MAP[icon || 'Package'] || Package;
}

// ── Install Dialog (两阶段：配置 → 流式进度) ──────────────────────

interface InstallDialogProps {
  item: MCPRegistryItem | null;
  open: boolean;
  onClose: () => void;
  onInstalled: () => void;
}

function InstallDialog({ item, open, onClose, onInstalled }: InstallDialogProps) {
  const [name, setName] = useState('');
  const [envVars, setEnvVars] = useState<Record<string, string>>({});
  const [sandboxId, setSandboxId] = useState<number>(0);
  const [sandboxes, setSandboxes] = useState<any[]>([]);
  const [phase, setPhase] = useState<'config' | 'installing'>('config');
  const [installPayload, setInstallPayload] = useState<any>(null);

  useEffect(() => {
    if (item) {
      setName(item.name);
      setEnvVars({});
      setPhase('config');
      setInstallPayload(null);
      try {
        const envs = JSON.parse(item.required_env || '[]');
        const initial: Record<string, string> = {};
        envs.forEach((e: any) => { initial[e.name] = ''; });
        setEnvVars(initial);
      } catch { /* ignore */ }
    }
  }, [item]);

  // 加载沙箱列表
  useEffect(() => {
    if (open) {
      client.get('/mcp-market/sandboxes').then(({ data }) => {
        setSandboxes(data || []);
        // 自动选中默认沙箱
        const defaultSb = (data || []).find((s: any) => s.is_default);
        if (defaultSb) setSandboxId(defaultSb.id);
      }).catch(() => {});
    }
  }, [open]);

  if (!item) return null;

  const requiredEnvList = (() => {
    try { return JSON.parse(item.required_env || '[]'); }
    catch { return []; }
  })();

  const dockerSandboxes = sandboxes.filter(s => s.supports_docker);

  const handleStartInstall = () => {
    const payload: any = { name: name.trim() };
    const filteredEnv = Object.fromEntries(
      Object.entries(envVars).filter(([, v]) => v.trim() !== '')
    );
    if (Object.keys(filteredEnv).length > 0) {
      payload.env_vars = filteredEnv;
    }
    if (sandboxId) {
      payload.sandbox_id = sandboxId;
    }
    setInstallPayload(payload);
    setPhase('installing');
  };

  const handleInstalled = () => {
    onInstalled();
  };

  // Phase 1: 配置表单
  if (phase === 'config') {
    return (
      <Dialog open={open} onOpenChange={v => !v && onClose()}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>安装 MCP 服务</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <div className="font-medium">{item.name}</div>
              <div className="text-sm text-muted-foreground">{item.package_name}</div>
              <div className="text-sm text-muted-foreground mt-1">{item.description}</div>
            </div>

            <div className="space-y-1.5">
              <Label>服务名称</Label>
              <Input value={name} onChange={e => setName(e.target.value)} placeholder="自定义名称" />
            </div>

            {dockerSandboxes.length > 0 && (
              <div className="space-y-1.5">
                <Label>安装环境</Label>
                <select
                  className="w-full border rounded-md px-3 py-2 text-sm bg-background"
                  value={sandboxId}
                  onChange={e => setSandboxId(Number(e.target.value))}
                >
                  {dockerSandboxes.map((sb: any) => (
                    <option key={sb.id} value={sb.id}>
                      {sb.display_name || sb.name} ({sb.sandbox_type})
                      {sb.is_default ? ' ⭐默认' : ''}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-muted-foreground">
                  Docker 镜像将在所选环境中构建和运行
                </p>
              </div>
            )}

            {requiredEnvList.length > 0 && (
              <div className="space-y-2">
                <Label>环境变量</Label>
                {requiredEnvList.map((env: any) => (
                  <div key={env.name} className="space-y-1">
                    <div className="flex items-center gap-2">
                      <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{env.name}</code>
                      {env.required === true && <span className="text-xs text-destructive">*</span>}
                    </div>
                    <Input
                      type="password"
                      value={envVars[env.name] || ''}
                      onChange={e => setEnvVars({ ...envVars, [env.name]: e.target.value })}
                      placeholder={env.desc || env.name}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={onClose}>取消</Button>
            <Button onClick={handleStartInstall} disabled={!name.trim()}>
              <Download className="h-4 w-4 mr-1" />
              开始安装
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  // Phase 2: 流式安装进度
  return (
    <McpInstallProgress
      open={open}
      onClose={onClose}
      registryId={item.id}
      payload={installPayload || {}}
      onInstalled={handleInstalled}
    />
  );
}

// ── npm Import Dialog ─────────────────────────────────────────────

interface NpmImportDialogProps {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}

function NpmImportDialog({ open, onClose, onImported }: NpmImportDialogProps) {
  const [keyword, setKeyword] = useState('');
  const [results, setResults] = useState<NpmResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);

  const handleSearch = async () => {
    if (!keyword.trim()) return;
    setSearching(true);
    try {
      const { data } = await client.get('/mcp-market/npm/search', {
        params: { keyword: keyword.trim(), size: 20 },
      });
      setResults(data.items || []);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '搜索失败');
    } finally {
      setSearching(false);
    }
  };

  const handleImport = async (pkg: NpmResult) => {
    setImporting(pkg.name);
    try {
      const { data } = await client.post('/mcp-market/npm/import', {
        name: pkg.name,
      });
      if (data.success) {
        toast.success(data.message || '导入成功');
        onImported();
      } else {
        toast.error(data.message || '导入失败');
      }
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '导入失败');
    } finally {
      setImporting(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>从 npm 导入 MCP 服务</DialogTitle>
        </DialogHeader>
        <div className="flex gap-2">
          <Input
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="搜索 npm 包（如 filesystem、github、database）"
            className="flex-1"
          />
          <Button onClick={handleSearch} disabled={searching}>
            {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          </Button>
        </div>
        <div className="flex-1 overflow-auto space-y-2 mt-2">
          {results.length === 0 && !searching && (
            <div className="text-sm text-muted-foreground text-center py-8">
              搜索 npm 上的 MCP 服务包
            </div>
          )}
          {results.map(pkg => (
            <div key={pkg.name} className="border rounded-lg p-3 hover:bg-muted/30 transition-colors">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-sm truncate">{pkg.name}</div>
                  <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{pkg.description}</div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <Badge variant="outline" className="text-xs">v{pkg.version}</Badge>
                    {pkg.author && <span className="text-xs text-muted-foreground">{pkg.author}</span>}
                    {pkg.homepage && (
                      <a href={pkg.homepage} target="_blank" rel="noopener noreferrer"
                        className="text-xs text-primary hover:underline flex items-center gap-0.5">
                        <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleImport(pkg)}
                  disabled={importing === pkg.name}
                >
                  {importing === pkg.name ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5 mr-1" />
                  )}
                  导入
                </Button>
              </div>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Main Component ────────────────────────────────────────────────

export default function MCPMarket() {
  const [items, setItems] = useState<MCPRegistryItem[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState('');
  const [activeCategory, setActiveCategory] = useState('');
  const [installItem, setInstallItem] = useState<MCPRegistryItem | null>(null);
  const [npmImportOpen, setNpmImportOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [listRes, catRes] = await Promise.all([
        client.get('/mcp-market/', {
          params: {
            category: activeCategory,
            keyword: keyword.trim(),
            size: 100,
          },
        }),
        client.get('/mcp-market/categories'),
      ]);
      setItems(listRes.data.items || []);
      setCategories(catRes.data || []);
    } catch {
      toast.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [activeCategory]);

  const handleSearch = () => {
    load();
  };

  const handleInstalled = () => {
    load(); // Refresh to update is_installed status
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="搜索 MCP 服务..."
            className="pl-8"
          />
        </div>
        <Button variant="outline" size="sm" onClick={() => setNpmImportOpen(true)}>
          <Package className="h-4 w-4 mr-1" />
          从 npm 导入
        </Button>
      </div>

      {/* Category Tabs */}
      <div className="flex flex-wrap gap-1.5">
        <Badge
          variant={activeCategory === '' ? 'default' : 'outline'}
          className="cursor-pointer hover:bg-primary/10 transition-colors"
          onClick={() => setActiveCategory('')}
        >
          全部
        </Badge>
        {categories.filter(c => c.count > 0).map(cat => {
          const Icon = getCategoryIcon(cat.icon);
          return (
            <Badge
              key={cat.key}
              variant={activeCategory === cat.key ? 'default' : 'outline'}
              className="cursor-pointer hover:bg-primary/10 transition-colors gap-1"
              onClick={() => setActiveCategory(cat.key)}
            >
              <Icon className="h-3 w-3" />
              {cat.label}
              <span className="text-xs opacity-60">{cat.count}</span>
            </Badge>
          );
        })}
      </div>

      {/* List */}
      <div className="grid gap-3">
        {loading && items.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}

        {!loading && items.length === 0 && (
          <div className="text-sm text-muted-foreground text-center py-12">
            {keyword ? '未找到匹配的 MCP 服务' : '暂无 MCP 服务，请从 npm 导入或运行 seed 脚本'}
          </div>
        )}

        {items.map(item => {
          const catInfo = categories.find(c => c.key === item.category);
          const CatIcon = getCategoryIcon(catInfo?.icon);

          return (
            <div
              key={item.id}
              className="border rounded-lg p-4 hover:bg-muted/30 transition-colors"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium">{item.name}</span>
                    {item.is_verified === 1 && (
                      <Badge variant="secondary" className="text-xs gap-0.5">
                        <Check className="h-3 w-3" />
                        官方
                      </Badge>
                    )}
                    {item.is_popular === 1 && (
                      <Badge variant="default" className="text-xs gap-0.5">
                        <Star className="h-3 w-3" />
                        推荐
                      </Badge>
                    )}
                    {item.is_installed && (
                      <Badge variant="outline" className="text-xs text-green-600 border-green-300">
                        已安装
                      </Badge>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1 font-mono">
                    {item.package_name}
                  </div>
                  <div className="text-sm text-muted-foreground mt-1.5">
                    {item.description}
                  </div>
                  <div className="flex items-center gap-3 mt-2 flex-wrap">
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <CatIcon className="h-3 w-3" />
                      {catInfo?.label || item.category}
                    </span>
                    <Badge variant="outline" className="text-xs">{item.install_type}</Badge>
                    {item.author && (
                      <span className="text-xs text-muted-foreground">by {item.author}</span>
                    )}
                    {item.stars > 0 && (
                      <span className="flex items-center gap-0.5 text-xs text-muted-foreground">
                        <Star className="h-3 w-3" />
                        {item.stars >= 1000 ? `${(item.stars / 1000).toFixed(1)}k` : item.stars}
                      </span>
                    )}
                    {item.homepage && (
                      <a
                        href={item.homepage}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-primary hover:underline flex items-center gap-0.5"
                      >
                        <ExternalLink className="h-3 w-3" />
                        主页
                      </a>
                    )}
                  </div>
                  {item.tags && (
                    <div className="flex gap-1 mt-2 flex-wrap">
                      {item.tags.split(',').slice(0, 6).map(tag => (
                        <Badge key={tag} variant="secondary" className="text-xs opacity-60">
                          {tag.trim()}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex-shrink-0">
                  {item.is_installed ? (
                    <Button size="sm" variant="outline" disabled>
                      <Check className="h-4 w-4 mr-1" />
                      已安装
                    </Button>
                  ) : (
                    <Button size="sm" onClick={() => setInstallItem(item)}>
                      <Download className="h-4 w-4 mr-1" />
                      安装
                    </Button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Install Dialog */}
      <InstallDialog
        item={installItem}
        open={!!installItem}
        onClose={() => setInstallItem(null)}
        onInstalled={handleInstalled}
      />

      {/* npm Import Dialog */}
      <NpmImportDialog
        open={npmImportOpen}
        onClose={() => setNpmImportOpen(false)}
        onImported={handleInstalled}
      />
    </div>
  );
}
