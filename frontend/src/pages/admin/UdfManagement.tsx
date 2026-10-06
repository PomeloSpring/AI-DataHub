import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import { RefreshCw, FunctionSquare, Search, Copy, Eye, Link2 } from 'lucide-react';
import { udfApi, type Udf } from '@/api/udf';

// UDF 目录页（只读）：查看同步任务 / SQL 任务 / DAG 可引用的可用 UDF。
// UDF 的上传与编辑暂不开放（由平台侧预置注册，adh_udfs 多版本管理）。

function usageExample(udf: Udf): string {
  const args = (udf.params || []).map(p => p.name).join(', ') || 'x';
  return `SELECT ${udf.name}(${args}) AS result FROM your_table LIMIT 100`;
}

function refExample(udf: Udf): string {
  return `["${udf.name}"]  或锁定版本  ["${udf.name}:${udf.version}"]`;
}

export default function UdfManagement() {
  const [udfs, setUdfs] = useState<Udf[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [detail, setDetail] = useState<Udf | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await udfApi.list();
      setUdfs(data.items || []);
    } catch {
      toast.error('UDF 列表加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return udfs;
    return udfs.filter(u =>
      u.name.toLowerCase().includes(kw)
      || (u.description || '').toLowerCase().includes(kw)
      || (u.expression || '').toLowerCase().includes(kw));
  }, [udfs, keyword]);

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => toast.success('已复制'));
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <FunctionSquare className="w-6 h-6 text-primary" />UDF 管理
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            数据同步 / SQL 任务可引用的可用函数目录（SQL 表达式函数）。在任务的转换 SQL 中直接调用，
            并在 udf_refs 中声明引用；上传与编辑暂不开放，由平台侧预置注册。
          </p>
        </div>
        <div className="flex gap-2">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-2.5 top-2.5 text-muted-foreground" />
            <Input className="pl-8 w-[220px]" placeholder="搜索名称 / 说明 / 表达式"
              value={keyword} onChange={e => setKeyword(e.target.value)} />
          </div>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />刷新
          </Button>
        </div>
      </div>

      <div className="border rounded-lg overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>表达式</TableHead>
              <TableHead>参数</TableHead>
              <TableHead>返回类型</TableHead>
              <TableHead>版本</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>引用次数</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={8} className="text-center p-8 text-muted-foreground">加载中...</TableCell></TableRow>
            ) : filtered.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center p-8 text-muted-foreground">
                {keyword ? '没有匹配的 UDF' : '暂无可用 UDF'}
              </TableCell></TableRow>
            ) : filtered.map(udf => (
              <TableRow key={`${udf.name}-${udf.version}`}>
                <TableCell>
                  <div className="font-medium">{udf.name}</div>
                  {udf.description && (
                    <div className="text-xs text-muted-foreground truncate max-w-[220px]">{udf.description}</div>
                  )}
                </TableCell>
                <TableCell>
                  <code className="text-xs bg-muted px-2 py-1 rounded block max-w-[280px] truncate">
                    {udf.expression}
                  </code>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {(udf.params || []).map(p => (
                      <Badge key={p.name} variant="outline" className="text-[10px]">{p.name}:{p.type || 'any'}</Badge>
                    ))}
                    {(udf.params || []).length === 0 && <span className="text-xs text-muted-foreground">无参数</span>}
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">{udf.return_type || 'auto'}</TableCell>
                <TableCell>v{udf.version}</TableCell>
                <TableCell>
                  {udf.is_active
                    ? <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20">可用</Badge>
                    : <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">已停用</Badge>}
                </TableCell>
                <TableCell>{udf.usage_count ?? 0}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" title="用法详情" onClick={() => setDetail(udf)}>
                      <Eye className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" title="复制调用示例"
                      onClick={() => copy(usageExample(udf))}>
                      <Copy className="w-4 h-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <p className="text-xs text-muted-foreground">
        在「同步任务」的转换 SQL 或 DAG「SQL 任务」节点中调用 UDF 后，需在任务配置的 udf_refs 中声明引用（声明式引用，血缘可追溯）。
      </p>

      {/* 用法详情 */}
      <Dialog open={!!detail} onOpenChange={() => setDetail(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FunctionSquare className="w-4 h-4" />{detail?.name}
              {detail && <Badge variant="outline" className="text-xs">v{detail.version}</Badge>}
              {detail && (detail.is_active
                ? <Badge variant="outline" className="text-xs bg-green-500/10 text-green-500 border-green-500/20">可用</Badge>
                : <Badge variant="outline" className="text-xs bg-gray-500/10 text-gray-500 border-gray-500/20">已停用</Badge>)}
            </DialogTitle>
          </DialogHeader>
          {detail && (
            <div className="space-y-4 text-sm">
              {detail.description && <p className="text-muted-foreground">{detail.description}</p>}

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">定义表达式</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 bg-muted px-3 py-2 rounded text-xs whitespace-pre-wrap">{detail.expression}</code>
                  <Button variant="outline" size="sm" onClick={() => copy(detail.expression)}>
                    <Copy className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">参数签名</p>
                <div className="flex flex-wrap gap-1">
                  {(detail.params || []).map(p => (
                    <Badge key={p.name} variant="secondary" className="text-xs">{p.name}:{p.type || 'any'}</Badge>
                  ))}
                  {(detail.params || []).length === 0 && <span className="text-xs text-muted-foreground">无参数</span>}
                  <Badge variant="outline" className="text-xs">返回 {detail.return_type || 'auto'}</Badge>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">调用示例（转换 SQL / SQL 任务）</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 bg-muted px-3 py-2 rounded text-xs whitespace-pre-wrap">
                    {usageExample(detail)}
                  </code>
                  <Button variant="outline" size="sm" onClick={() => copy(usageExample(detail))}>
                    <Copy className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">udf_refs 声明（任务配置）</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 bg-muted px-3 py-2 rounded text-xs">{refExample(detail)}</code>
                  <Button variant="outline" size="sm" onClick={() => copy(refExample(detail))}>
                    <Copy className="w-3.5 h-3.5" />
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  <Link2 className="w-3 h-3 inline mr-1" />
                  引用后自动累计调用次数；未在 udf_refs 声明的调用会被拒绝执行（显式报错，不静默跳过）。
                </p>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
