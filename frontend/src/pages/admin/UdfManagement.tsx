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
import { toast } from '@/lib/toast';
import { RefreshCw, FunctionSquare, Search, Copy, Eye, Link2 } from 'lucide-react';
import { udfApi, type Udf } from '@/api/udf';
import { useTranslation } from 'react-i18next';

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
  const { t } = useTranslation('ui');
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
      toast.error(t('k_03d6553fb8'));
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
    navigator.clipboard.writeText(text).then(() => toast.success(t('k_52e6abbe5c')));
  };

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <FunctionSquare className="w-6 h-6 text-primary" />{t('k_d98c658135')}
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            {t('k_32da88e581')}
          </p>
        </div>
        <div className="flex gap-2">
          <div className="relative">
            <Search className="w-4 h-4 absolute left-2.5 top-2.5 text-muted-foreground" />
            <Input className="pl-8 w-[220px]" placeholder={t("k_8bebd245af")}
              value={keyword} onChange={e => setKeyword(e.target.value)} />
          </div>
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />{t('k_38108eaa1d')}
          </Button>
        </div>
      </div>

      <div className="border rounded-lg overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("k_1be7ae4fc2")}</TableHead>
              <TableHead>{t("k_513c1c6355")}</TableHead>
              <TableHead>{t("k_749d765242")}</TableHead>
              <TableHead>{t("k_f9d641e510")}</TableHead>
              <TableHead>{t("k_989d1affa0")}</TableHead>
              <TableHead>{t("k_62e951a692")}</TableHead>
              <TableHead>{t("k_43d027bab3")}</TableHead>
              <TableHead className="text-right">{t("k_f3ea6d345e")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={8} className="text-center p-8 text-muted-foreground">{t('k_26b5bd4947')}</TableCell></TableRow>
            ) : filtered.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center p-8 text-muted-foreground">
                {keyword ? t('k_22d315aac4') : t('k_1524f8f191')}
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
                    {(udf.params || []).length === 0 && <span className="text-xs text-muted-foreground">{t("k_2f1ce71d41")}</span>}
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">{udf.return_type || 'auto'}</TableCell>
                <TableCell>v{udf.version}</TableCell>
                <TableCell>
                  {udf.is_active
                    ? <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20">{t("k_e91365cf9e")}</Badge>
                    : <Badge variant="outline" className="bg-gray-500/10 text-gray-500 border-gray-500/20">{t("k_6c7dcbb73a")}</Badge>}
                </TableCell>
                <TableCell>{udf.usage_count ?? 0}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" title={t("k_f4ecd6e748")} onClick={() => setDetail(udf)}>
                      <Eye className="w-4 h-4" />
                    </Button>
                    <Button variant="ghost" size="sm" title={t("k_f2b90f5a7f")}
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
        {t('k_db40dab827')}
      </p>

      {/* 用法详情 */}
      <Dialog open={!!detail} onOpenChange={() => setDetail(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FunctionSquare className="w-4 h-4" />{detail?.name}
              {detail && <Badge variant="outline" className="text-xs">v{detail.version}</Badge>}
              {detail && (detail.is_active
                ? <Badge variant="outline" className="text-xs bg-green-500/10 text-green-500 border-green-500/20">{t("k_e91365cf9e")}</Badge>
                : <Badge variant="outline" className="text-xs bg-gray-500/10 text-gray-500 border-gray-500/20">{t("k_6c7dcbb73a")}</Badge>)}
            </DialogTitle>
          </DialogHeader>
          {detail && (
            <div className="space-y-4 text-sm">
              {detail.description && <p className="text-muted-foreground">{detail.description}</p>}

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">{t("k_155a5f0a77")}</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 bg-muted px-3 py-2 rounded text-xs whitespace-pre-wrap">{detail.expression}</code>
                  <Button variant="outline" size="sm" onClick={() => copy(detail.expression)}>
                    <Copy className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">{t("k_90c24ec8da")}</p>
                <div className="flex flex-wrap gap-1">
                  {(detail.params || []).map(p => (
                    <Badge key={p.name} variant="secondary" className="text-xs">{p.name}:{p.type || 'any'}</Badge>
                  ))}
                  {(detail.params || []).length === 0 && <span className="text-xs text-muted-foreground">{t("k_2f1ce71d41")}</span>}
                  <Badge variant="outline" className="text-xs">{t('k_7ba789bded', { p0: detail.return_type || 'auto' })}</Badge>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">{t("k_6acbccafe6")}</p>
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
                <p className="text-xs font-medium text-muted-foreground mb-1">{t('k_b5fe8c633c')}</p>
                <div className="flex items-start gap-2">
                  <code className="flex-1 bg-muted px-3 py-2 rounded text-xs">{refExample(detail)}</code>
                  <Button variant="outline" size="sm" onClick={() => copy(refExample(detail))}>
                    <Copy className="w-3.5 h-3.5" />
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  <Link2 className="w-3 h-3 inline mr-1" />
                  {t('k_f2eae7b145')}
                </p>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
