import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookmarkPlus, Check, Copy, Database, Loader2, SquareArrowOutUpRight } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import client from '@/api/client';

interface Props {
  code: string;
  /** 预览区高度上限(px),超出滚动 */
  maxHeight?: number;
}

/**
 * SQL 卡片:聊天回答中的 ```sql 块与结果卡 SQL 页共用。
 * 动作:复制到剪贴板 / 在 Playground 打开(经 sessionStorage 预填编辑器后跳转)
 *      / 存为数据集(已治理的查询资产, Chat/看板/Playground 共用一份定义)。
 */
export default function SqlCard({ code, maxHeight = 320 }: Props) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const [tplOpen, setTplOpen] = useState(false);
  const [tplName, setTplName] = useState('');
  const [savingTpl, setSavingTpl] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      toast.success('SQL 已复制');
    } catch {
      toast.error('复制失败,请手动选择内容复制');
    }
  };

  const openPlayground = () => {
    sessionStorage.setItem('playground_sql', JSON.stringify({ sql: code }));
    navigate('/data/playground');
  };

  // 沉淀为查询数据集: 数据集是平台内唯一的"可信任预制 SQL"载体，
  // 可在数据集页补数据源/字段定义/行级范围后供 Chat 与看板取数。
  const saveDataset = async () => {
    if (!tplName.trim() || savingTpl) return;
    setSavingTpl(true);
    try {
      await client.post('/datasets', { name: tplName.trim(), sql_query: code });
      toast.success(`已存为数据集「${tplName.trim()}」，请到数据集页选定执行数据源与字段定义`);
      setTplOpen(false);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '存数据集失败，请稍后重试');
    } finally {
      setSavingTpl(false);
    }
  };

  return (
    <div className="my-3 overflow-hidden rounded-lg border bg-card">
      <div className="flex items-center gap-2 border-b bg-muted/50 px-3 py-2">
        <Database className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold">SQL</span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={copy}>
            {copied ? <Check className="h-3 w-3 mr-1" /> : <Copy className="h-3 w-3 mr-1" />}
            复制
          </Button>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={openPlayground}>
            <SquareArrowOutUpRight className="h-3 w-3 mr-1" />
            在 Playground 打开
          </Button>
          <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => {
            setTplName(`查询数据集 ${new Date().toISOString().slice(0, 10)}`);
            setTplOpen(true);
          }}>
            <BookmarkPlus className="h-3 w-3 mr-1" />
            存为数据集
          </Button>
        </div>
      </div>
      <pre
        className="overflow-auto bg-muted/30 p-3 text-xs leading-relaxed font-mono"
        style={{ maxHeight }}
      >
        {code}
      </pre>

      <Dialog open={tplOpen} onOpenChange={setTplOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>存为查询数据集</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">数据集名称（全局唯一，在数据集页与 Chat 中展示）</label>
            <Input value={tplName} onChange={e => setTplName(e.target.value)} placeholder="如：销售日报核心查询" autoFocus />
            <p className="text-[11px] text-muted-foreground">
              存入后由服务端执行只读校验；请在数据集页选定执行数据源并声明维度/度量，
              之后 Chat、看板与 Playground 共用同一份已治理定义。
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setTplOpen(false)}>取消</Button>
            <Button size="sm" onClick={() => void saveDataset()} disabled={savingTpl || !tplName.trim()}>
              {savingTpl && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
              存入数据集
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
