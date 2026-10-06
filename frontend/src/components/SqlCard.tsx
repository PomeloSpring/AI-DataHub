import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookmarkPlus, Check, Copy, Database, Loader2, SquareArrowOutUpRight } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { createVisComponent } from '@/api/visLibrary';

interface Props {
  code: string;
  /** 预览区高度上限(px),超出滚动 */
  maxHeight?: number;
}

/**
 * SQL 卡片:聊天回答中的 ```sql 块与结果卡 SQL 页共用。
 * 动作:复制到剪贴板 / 在 Playground 打开(经 sessionStorage 预填编辑器后跳转)。
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

  // 沉淀为字模库 sql_template(可视化配置):可在字模库管理与 Playground「SQL 模板」复用
  const saveTemplate = async () => {
    if (!tplName.trim() || savingTpl) return;
    setSavingTpl(true);
    try {
      await createVisComponent({
        name: tplName.trim(),
        category: 'sql_template',
        style_config: {},
        query_template: { sql: code },
      });
      toast.success(`已存入字模库 SQL 模板「${tplName.trim()}」，可在 Playground 侧栏复用`);
      setTplOpen(false);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || '存模板失败，请稍后重试');
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
            setTplName(`SQL 模板 ${new Date().toISOString().slice(0, 10)}`);
            setTplOpen(true);
          }}>
            <BookmarkPlus className="h-3 w-3 mr-1" />
            存为模板
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
            <DialogTitle>存为字模库 SQL 模板</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">模板名称（在字模库与 Playground 中展示）</label>
            <Input value={tplName} onChange={e => setTplName(e.target.value)} placeholder="如：销售日报核心查询" autoFocus />
            <p className="text-[11px] text-muted-foreground">
              将以 sql_template 字模存入字模库（query_template），可在 Playground 侧栏一键插入。
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setTplOpen(false)}>取消</Button>
            <Button size="sm" onClick={() => void saveTemplate()} disabled={savingTpl || !tplName.trim()}>
              {savingTpl && <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />}
              存入字模库
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
