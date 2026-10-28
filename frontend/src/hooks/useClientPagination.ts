import { useMemo, useState, useEffect } from 'react';

/**
 * 客户端分页：对「后端一次性返回全量、数据量中小」的配置类列表做本地切片分页。
 *
 * 与 <PaginationBar> 配套使用：
 *   const { page, pageSize, total, slice, setPage } = useClientPagination(items, 20);
 *   ...{slice.map(renderCard)}...
 *   <PaginationBar page={page} pageSize={pageSize} total={total} onChange={setPage} />
 *
 * 注意：仅用于列表本身已全量在前端的场景（数据集/看板/标签/字模/站点等配置清单）。
 * 大表（日志、审计、指标字典等）必须走服务端分页，不得用本 hook 假装分页。
 * 依赖列表长度变化（筛选/新建/删除）时自动回到第 1 页，避免停在越界空页。
 */
export interface ClientPagination<T> {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  /** 当前页切片（可直接 .map 渲染） */
  slice: T[];
  setPage: (p: number) => void;
  /** 切换每页条数（自动回到第 1 页） */
  setPageSize: (n: number) => void;
  /** 手动重置到第 1 页（筛选条件变化时调用） */
  reset: () => void;
}

export function useClientPagination<T>(
  items: readonly T[],
  initialPageSize = 20,
): ClientPagination<T> {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSizeState] = useState(initialPageSize);
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));

  // 数据量变化（筛选/增删）导致当前页越界时收敛，杜绝停在空白页
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const setPageSize = (n: number) => {
    setPageSizeState(n);
    setPage(1);
  };

  const slice = useMemo(() => {
    const start = (page - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, page, pageSize]);

  return {
    page,
    pageSize,
    total,
    totalPages,
    slice,
    setPage,
    setPageSize,
    reset: () => setPage(1),
  };
}

export default useClientPagination;
