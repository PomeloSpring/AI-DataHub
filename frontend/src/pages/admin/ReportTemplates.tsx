// 模板中心：报告模板（版式契约）+ 消息模板（IM / 邮件 / 短信文案）同页分类切换。
//
// 为什么合在一页：这三类东西过去散在两个地方 —— 报告模板在「报告模板」页，
// 通知文案内联在渠道 config 里手填（而且发送侧根本不读）。合到一处后，
// "报告长什么样"与"通知怎么说"都在模板中心维护，渠道与任务只留引用。
//
// 布局一律表格：卡片把缩略图占成主角，真正决定生不生效的版式摘要、被谁引用反而看不清。
//
// 报告模板三面共用同一份版式契约：
//   ① 定时任务：任务表单选模板 → 每次执行按模板版式出报告；
//   ② 报告中心：「套用模板」按版式起稿，「另存为模板」把好报告版式沉淀回这里；
//   ③ 报告「用最新数据重新生成」：沿用上一版版式（同一 layout 形状与校验）。
// 消息模板则被通知渠道引用（config.message_template_key），发送时按当轮执行结果展开。
import { useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Layers, MessageSquare, Mail, Smartphone } from 'lucide-react';
import ReportTemplatePanel from './templates/ReportTemplatePanel';
import MessageTemplatePanel from './templates/MessageTemplatePanel';

export default function TemplateCenter() {
  const [tab, setTab] = useState('report');

  return (
    <div className="p-6 space-y-4 overflow-y-auto">
      <div>
        <h1 className="text-xl font-bold flex items-center gap-2"><Layers className="h-5 w-5" /> 模板中心</h1>
        <p className="text-sm text-muted-foreground mt-1">
          报告模板管版式（章节 + 图表规格 + 主题），消息模板管通知文案（按变量展开）。
          模板只存结构与文案，不存数据；任务与渠道选的都只是引用，改一处即生效。
        </p>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="report"><Layers className="h-4 w-4 mr-1" />报告模板</TabsTrigger>
          <TabsTrigger value="im"><MessageSquare className="h-4 w-4 mr-1" />群机器人 / Webhook</TabsTrigger>
          <TabsTrigger value="email"><Mail className="h-4 w-4 mr-1" />邮件</TabsTrigger>
          <TabsTrigger value="sms"><Smartphone className="h-4 w-4 mr-1" />短信</TabsTrigger>
        </TabsList>
        {/* 非当前分类不挂载：切回来时重新按服务端口径取数，免得留着旧行编辑到已下架的模板 */}
        <TabsContent value="report"><ReportTemplatePanel /></TabsContent>
        {tab === 'im' && <TabsContent value="im"><MessageTemplatePanel kind="im" /></TabsContent>}
        {tab === 'email' && <TabsContent value="email"><MessageTemplatePanel kind="email" /></TabsContent>}
        {tab === 'sms' && <TabsContent value="sms"><MessageTemplatePanel kind="sms" /></TabsContent>}
      </Tabs>
    </div>
  );
}
