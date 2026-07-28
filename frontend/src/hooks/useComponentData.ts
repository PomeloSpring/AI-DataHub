import { useState, useEffect, useCallback, useRef } from 'react';
import client from '../api/client';

interface ComponentDataOptions {
  page?: number;
  size?: number;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  agg_method?: string;
  group_by?: string;
}

interface UseComponentDataParams {
  datasource_id: number;
  sql: string;
  params?: Record<string, any>;
  component_type: string;
  options?: ComponentDataOptions;
  enabled?: boolean;
}

interface UseComponentDataResult {
  data: any[];
  total: number | null;
  columns: { name: string; type?: string }[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useComponentData({
  datasource_id,
  sql,
  params,
  component_type,
  options,
  enabled = true,
}: UseComponentDataParams): UseComponentDataResult {
  const [data, setData] = useState<any[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [columns, setColumns] = useState<{ name: string; type?: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mountedRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);

  // Stable serialization for dependency tracking
  const paramsKey = params ? JSON.stringify(params) : '';
  const optionsKey = options ? JSON.stringify(options) : '';

  const fetchData = useCallback(async () => {
    if (!enabled || !sql) {
      setData([]);
      setTotal(null);
      setColumns([]);
      setError(null);
      return;
    }

    // Cancel any in-flight request
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    try {
      const body: Record<string, any> = {
        datasource_id,
        sql,
        component_type,
      };
      if (params) body.params = params;
      if (options) body.options = options;

      const res = await client.post('/component-data', body, {
        signal: controller.signal,
      });

      if (!mountedRef.current) return;

      const result = res.data;
      setData(result.data ?? []);
      setTotal(result.total ?? null);
      setColumns(result.columns ?? []);
    } catch (err: any) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') return;
      if (!mountedRef.current) return;

      const message =
        err?.response?.data?.detail ||
        err?.message ||
        'Failed to fetch component data';
      setError(typeof message === 'string' ? message : JSON.stringify(message));
      setData([]);
      setTotal(null);
      setColumns([]);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [datasource_id, sql, paramsKey, optionsKey, component_type, enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchData();

    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, [fetchData]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(() => {
    fetchData();
  }, [fetchData]);

  return { data, total, columns, loading, error, refresh };
}
