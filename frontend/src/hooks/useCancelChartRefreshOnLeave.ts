import { useEffect } from 'react';
import { useDashboardStore } from '@/stores/dashboardStore';

/**
 * 离开看板视图时终止未完成的图表查询。
 *
 * 「未执行的」排队查询直接丢弃，「执行中的」直接 abort，避免连接被继续占用 ——
 * 曾经退出页面后查询仍在跑，把浏览器每源连接占满，导致其他功能一直 pending。
 * `pagehide` 兜住关标签页/整页跳转这类组件卸载不一定触发的场景。
 */
export function useCancelChartRefreshOnLeave() {
  useEffect(() => {
    const cancel = () => useDashboardStore.getState().cancelChartRefresh();
    window.addEventListener('pagehide', cancel);
    return () => {
      window.removeEventListener('pagehide', cancel);
      cancel();
    };
  }, []);
}
