import { useState, useCallback } from 'react';
import {
  Layout, BarChart3, LineChart, LayoutDashboard, Rocket, Star,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';

interface DashboardTemplate {
  id: string;
  name: string;
  description: string;
  icon: React.ReactNode;
  tags: string[];
  charts: {
    name: string;
    chart_type: string;
    position: { x: number; y: number; w: number; h: number };
    config?: Record<string, any>;
  }[];
  preview?: string;
}

// Canvas is 1920px wide, 12 columns = 160px each
// KPI cards: 160*3=480px wide, 200px tall
// Charts: 160*8=1280px or 160*4=640px wide, 400px tall
const COL = 160;
const KPI_H = 200;
const CHART_H = 400;

const DEFAULT_TEMPLATES: DashboardTemplate[] = [
  {
    id: 'sales_overview',
    name: '销售概览',
    description: '展示销售数据的关键指标，包括销售额、订单量、客户数等',
    icon: <BarChart3 className="h-6 w-6 text-blue-500" />,
    tags: ['销售', 'KPI', '概览'],
    charts: [
      { name: '总销售额', chart_type: 'big_number_trend', position: { x: 0, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT CONCAT(ROUND(SUM(amount)/10000, 2), '万') as value, '总销售额' as label FROM orders WHERE date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)", xCol: 'label', yCol: 'value' } },
      { name: '订单量', chart_type: 'big_number_trend', position: { x: COL*3, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(*) as value, '订单量' as label FROM orders WHERE date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)", xCol: 'label', yCol: 'value' } },
      { name: '客户数', chart_type: 'big_number_trend', position: { x: COL*6, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(DISTINCT customer_id) as value, '客户数' as label FROM orders WHERE date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)", xCol: 'label', yCol: 'value' } },
      { name: '转化率', chart_type: 'gauge', position: { x: COL*9, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND(COUNT(CASE WHEN status='completed' THEN 1 END) * 100.0 / COUNT(*), 1) as value FROM orders", yCol: 'value', max: 100 } },
      { name: '销售趋势', chart_type: 'line', position: { x: 0, y: KPI_H+20, w: COL*8, h: CHART_H }, config: { sql: "SELECT DATE_FORMAT(date, '%m-%d') as dt, SUM(amount) as total FROM orders WHERE date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) GROUP BY date ORDER BY date", xCol: 'dt', yCol: 'total' } },
      { name: '产品分布', chart_type: 'pie', position: { x: COL*8, y: KPI_H+20, w: COL*4, h: CHART_H }, config: { sql: "SELECT product_name as name, COUNT(*) as value FROM orders GROUP BY product_name ORDER BY value DESC LIMIT 10", nameCol: 'name', yCol: 'value' } },
    ],
  },
  {
    id: 'traffic_analysis',
    name: '流量分析',
    description: '分析网站流量数据，包括PV/UV、来源分布、页面访问等',
    icon: <LineChart className="h-6 w-6 text-green-500" />,
    tags: ['流量', '网站', '分析'],
    charts: [
      { name: '今日PV', chart_type: 'big_number_trend', position: { x: 0, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(*) as value, '今日PV' as label FROM access_log WHERE date = CURDATE()", xCol: 'label', yCol: 'value' } },
      { name: '今日UV', chart_type: 'big_number_trend', position: { x: COL*3, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(DISTINCT user_id) as value, '今日UV' as label FROM access_log WHERE date = CURDATE()", xCol: 'label', yCol: 'value' } },
      { name: '平均停留时间', chart_type: 'big_number_trend', position: { x: COL*6, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND(AVG(duration), 0) as value, '秒' as label FROM access_log WHERE date = CURDATE()", xCol: 'label', yCol: 'value' } },
      { name: '跳出率', chart_type: 'gauge', position: { x: COL*9, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND(COUNT(CASE WHEN page_count=1 THEN 1 END) * 100.0 / COUNT(*), 1) as value FROM access_log WHERE date = CURDATE()", yCol: 'value', max: 100 } },
      { name: '流量趋势', chart_type: 'area', position: { x: 0, y: KPI_H+20, w: COL*8, h: CHART_H }, config: { sql: "SELECT DATE_FORMAT(date, '%m-%d') as dt, COUNT(*) as pv, COUNT(DISTINCT user_id) as uv FROM access_log WHERE date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY date ORDER BY date", xCol: 'dt', yCol: 'pv' } },
      { name: '来源分布', chart_type: 'pie', position: { x: COL*8, y: KPI_H+20, w: COL*4, h: CHART_H }, config: { sql: "SELECT source as name, COUNT(*) as value FROM access_log WHERE date = CURDATE() GROUP BY source ORDER BY value DESC LIMIT 10", nameCol: 'name', yCol: 'value' } },
      { name: '热门页面', chart_type: 'bar', position: { x: 0, y: KPI_H+CHART_H+40, w: COL*6, h: CHART_H }, config: { sql: "SELECT page_url as name, COUNT(*) as value FROM access_log WHERE date = CURDATE() GROUP BY page_url ORDER BY value DESC LIMIT 10", xCol: 'name', yCol: 'value' } },
    ],
  },
  {
    id: 'financial_report',
    name: '财务报表',
    description: '财务数据展示，包括收入、支出、利润、现金流等关键指标',
    icon: <LayoutDashboard className="h-6 w-6 text-yellow-500" />,
    tags: ['财务', '报表', '月度'],
    charts: [
      { name: '总收入', chart_type: 'big_number_trend', position: { x: 0, y: 0, w: COL*4, h: KPI_H }, config: { sql: "SELECT CONCAT(ROUND(SUM(income)/10000, 2), '万') as value, '总收入' as label FROM finance WHERE month = DATE_FORMAT(CURDATE(), '%Y-%m')", xCol: 'label', yCol: 'value' } },
      { name: '总支出', chart_type: 'big_number_trend', position: { x: COL*4, y: 0, w: COL*4, h: KPI_H }, config: { sql: "SELECT CONCAT(ROUND(SUM(expense)/10000, 2), '万') as value, '总支出' as label FROM finance WHERE month = DATE_FORMAT(CURDATE(), '%Y-%m')", xCol: 'label', yCol: 'value' } },
      { name: '净利润', chart_type: 'big_number_trend', position: { x: COL*8, y: 0, w: COL*4, h: KPI_H }, config: { sql: "SELECT CONCAT(ROUND((SUM(income)-SUM(expense))/10000, 2), '万') as value, '净利润' as label FROM finance WHERE month = DATE_FORMAT(CURDATE(), '%Y-%m')", xCol: 'label', yCol: 'value' } },
      { name: '收支趋势', chart_type: 'line', position: { x: 0, y: KPI_H+20, w: COL*8, h: CHART_H }, config: { sql: "SELECT month as dt, SUM(income) as income, SUM(expense) as expense FROM finance GROUP BY month ORDER BY month DESC LIMIT 12", xCol: 'dt', yCol: 'income' } },
      { name: '支出分布', chart_type: 'pie', position: { x: COL*8, y: KPI_H+20, w: COL*4, h: CHART_H }, config: { sql: "SELECT category as name, SUM(expense) as value FROM finance WHERE month = DATE_FORMAT(CURDATE(), '%Y-%m') GROUP BY category", nameCol: 'name', yCol: 'value' } },
      { name: '月度对比', chart_type: 'bar', position: { x: 0, y: KPI_H+CHART_H+40, w: COL*12, h: CHART_H }, config: { sql: "SELECT month as name, SUM(income) - SUM(expense) as value FROM finance GROUP BY month ORDER BY month DESC LIMIT 6", xCol: 'name', yCol: 'value' } },
    ],
  },
  {
    id: 'user_analytics',
    name: '用户分析',
    description: '用户行为分析，包括新增用户、活跃用户、留存率等',
    icon: <Star className="h-6 w-6 text-pink-500" />,
    tags: ['用户', '行为', '留存'],
    charts: [
      { name: '总用户数', chart_type: 'big_number_trend', position: { x: 0, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(*) as value, '总用户' as label FROM users", xCol: 'label', yCol: 'value' } },
      { name: '今日新增', chart_type: 'big_number_trend', position: { x: COL*3, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(*) as value, '今日新增' as label FROM users WHERE DATE(created_at) = CURDATE()", xCol: 'label', yCol: 'value' } },
      { name: '活跃用户', chart_type: 'big_number_trend', position: { x: COL*6, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT COUNT(DISTINCT user_id) as value, '活跃用户' as label FROM user_actions WHERE date = CURDATE()", xCol: 'label', yCol: 'value' } },
      { name: '留存率', chart_type: 'gauge', position: { x: COL*9, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND(COUNT(DISTINCT CASE WHEN last_active >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN user_id END) * 100.0 / COUNT(DISTINCT user_id), 1) as value FROM users", yCol: 'value', max: 100 } },
      { name: '用户增长趋势', chart_type: 'area', position: { x: 0, y: KPI_H+20, w: COL*8, h: CHART_H }, config: { sql: "SELECT DATE_FORMAT(created_at, '%m-%d') as dt, COUNT(*) as new_users FROM users WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) GROUP BY DATE(created_at) ORDER BY DATE(created_at)", xCol: 'dt', yCol: 'new_users' } },
      { name: '用户画像', chart_type: 'radar', position: { x: COL*8, y: KPI_H+20, w: COL*4, h: CHART_H }, config: { sql: "SELECT '18-25' as name, COUNT(*) as value FROM users WHERE age BETWEEN 18 AND 25 UNION ALL SELECT '26-35', COUNT(*) FROM users WHERE age BETWEEN 26 AND 35 UNION ALL SELECT '36-45', COUNT(*) FROM users WHERE age BETWEEN 36 AND 45 UNION ALL SELECT '46+', COUNT(*) FROM users WHERE age > 45", nameCol: 'name', yCol: 'value' } },
    ],
  },
  {
    id: 'performance_monitor',
    name: '性能监控',
    description: '系统性能监控，包括响应时间、错误率、吞吐量等',
    icon: <Rocket className="h-6 w-6 text-purple-500" />,
    tags: ['监控', '性能', '运维'],
    charts: [
      { name: '平均响应时间', chart_type: 'big_number_trend', position: { x: 0, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT CONCAT(ROUND(AVG(response_time), 0), 'ms') as value, '响应时间' as label FROM api_logs WHERE time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)", xCol: 'label', yCol: 'value' } },
      { name: '错误率', chart_type: 'gauge', position: { x: COL*3, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND(COUNT(CASE WHEN status_code >= 500 THEN 1 END) * 100.0 / COUNT(*), 2) as value FROM api_logs WHERE time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)", yCol: 'value', max: 100 } },
      { name: 'QPS', chart_type: 'big_number_trend', position: { x: COL*6, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND(COUNT(*) / 3600.0, 1) as value, 'QPS' as label FROM api_logs WHERE time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)", xCol: 'label', yCol: 'value' } },
      { name: '可用性', chart_type: 'gauge', position: { x: COL*9, y: 0, w: COL*3, h: KPI_H }, config: { sql: "SELECT ROUND((1 - COUNT(CASE WHEN status_code >= 500 THEN 1 END) * 1.0 / COUNT(*)) * 100, 2) as value FROM api_logs WHERE time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)", yCol: 'value', max: 100 } },
      { name: '响应时间趋势', chart_type: 'line', position: { x: 0, y: KPI_H+20, w: COL*8, h: CHART_H }, config: { sql: "SELECT DATE_FORMAT(time, '%H:%i') as dt, ROUND(AVG(response_time), 0) as avg_time FROM api_logs WHERE time >= DATE_SUB(NOW(), INTERVAL 1 HOUR) GROUP BY FLOOR(UNIX_TIMESTAMP(time)/300) ORDER BY MIN(time)", xCol: 'dt', yCol: 'avg_time' } },
      { name: '错误分布', chart_type: 'pie', position: { x: COL*8, y: KPI_H+20, w: COL*4, h: CHART_H }, config: { sql: "SELECT CONCAT(status_code, '') as name, COUNT(*) as value FROM api_logs WHERE status_code >= 400 AND time >= DATE_SUB(NOW(), INTERVAL 1 HOUR) GROUP BY status_code", nameCol: 'name', yCol: 'value' } },
      { name: '请求量趋势', chart_type: 'area', position: { x: 0, y: KPI_H+CHART_H+40, w: COL*12, h: CHART_H }, config: { sql: "SELECT DATE_FORMAT(time, '%H:%i') as dt, COUNT(*) as requests FROM api_logs WHERE time >= DATE_SUB(NOW(), INTERVAL 1 HOUR) GROUP BY FLOOR(UNIX_TIMESTAMP(time)/300) ORDER BY MIN(time)", xCol: 'dt', yCol: 'requests' } },
    ],
  },
  {
    id: 'empty',
    name: '空白仪表盘',
    description: '从零开始创建自定义仪表盘',
    icon: <Layout className="h-6 w-6 text-muted-foreground" />,
    tags: ['自定义', '空白'],
    charts: [],
  },
];

interface DashboardTemplatesProps {
  open: boolean;
  onClose: () => void;
  onApply: (template: DashboardTemplate) => void;
}

export default function DashboardTemplates({ open, onClose, onApply }: DashboardTemplatesProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<DashboardTemplate | null>(null);
  const [searchText, setSearchText] = useState('');

  const filteredTemplates = DEFAULT_TEMPLATES.filter(template =>
    template.name.toLowerCase().includes(searchText.toLowerCase()) ||
    template.description.toLowerCase().includes(searchText.toLowerCase()) ||
    template.tags.some(tag => tag.toLowerCase().includes(searchText.toLowerCase()))
  );

  const handleApply = useCallback(() => {
    if (selectedTemplate) {
      onApply(selectedTemplate);
      onClose();
      setSelectedTemplate(null);
    }
  }, [selectedTemplate, onApply, onClose]);

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-[800px]">
        <DialogHeader>
          <DialogTitle>选择仪表盘模板</DialogTitle>
        </DialogHeader>

        <div className="mb-4">
          <Input
            placeholder="搜索模板..."
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            className="max-w-[300px]"
          />
        </div>

        {filteredTemplates.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">未找到匹配的模板</div>
        ) : (
          <ScrollArea className="h-[400px]">
            <div className="grid grid-cols-3 gap-4">
              {filteredTemplates.map(template => (
                <div
                  key={template.id}
                  className={`p-4 rounded-lg border cursor-pointer transition-all ${
                    selectedTemplate?.id === template.id
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:border-primary/50'
                  }`}
                  onClick={() => setSelectedTemplate(template)}
                >
                  <div className="flex items-start gap-3 mb-3">
                    <div className="w-12 h-12 rounded-lg bg-muted flex items-center justify-center">
                      {template.icon}
                    </div>
                    <div className="flex-1 min-w-0">
                      <h3 className="font-semibold text-sm">{template.name}</h3>
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{template.description}</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-1 mb-2">
                    {template.tags.map(tag => (
                      <Badge key={tag} variant="outline" className="text-xs">{tag}</Badge>
                    ))}
                  </div>

                  {template.charts.length > 0 && (
                    <span className="text-xs text-muted-foreground">
                      包含 {template.charts.length} 个图表
                    </span>
                  )}
                </div>
              ))}
            </div>
          </ScrollArea>
        )}

        {selectedTemplate && (
          <div className="flex items-center justify-between p-4 bg-muted rounded-lg mt-4">
            <div>
              <span className="font-medium">已选择: {selectedTemplate.name}</span>
              <span className="text-sm text-muted-foreground ml-2">
                {selectedTemplate.charts.length > 0
                  ? `将创建 ${selectedTemplate.charts.length} 个图表`
                  : '空白仪表盘'}
              </span>
            </div>
            <Button onClick={handleApply}>使用此模板</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
