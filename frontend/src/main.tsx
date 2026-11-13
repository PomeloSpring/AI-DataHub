import React from 'react';
import ReactDOM from 'react-dom/client';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppToaster } from '@/lib/toast';
import App from './App';
import { restoreBrandSnapshot } from './lib/brand';
import './styles/globals.css';

// 渲染前按本地缓存同步恢复品牌标题/图标，消除刷新时"先闪默认名再切配置名"的跳字
restoreBrandSnapshot();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <TooltipProvider>
      <App />
      {/* toast 的驻留时长/可关闭性/堆叠数只在 lib/toast.tsx 一处定义，改那里全端生效 */}
      <AppToaster />
    </TooltipProvider>
  </React.StrictMode>
);
