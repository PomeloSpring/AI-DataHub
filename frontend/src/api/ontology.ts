import client from './client';

export type OntologyStatus = 'draft' | 'active' | 'archived';

export interface OntologyModelSummary {
  id: number;
  datasource_id: number;
  name: string;
  status: OntologyStatus;
  object_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface OntologyModel extends OntologyModelSummary {
  json_content: string;
  yaml_content: string;
  md_content: string;
}

export interface OntologyObjectHit {
  object_key: string;
  display_name: string;
  aliases: string;
  description: string;
  distance: number | null;
  object: Record<string, any> | null;
}

export interface GenerateProgress {
  stage: string;
  detail: string;
}

/** SSE 生成本体草案，逐事件回调，返回 AbortController 供取消 */
export function generateOntologyDraft(
  datasourceId: number,
  onEvent: (event: 'progress' | 'done' | 'error', data: any) => void,
): AbortController {
  const abort = new AbortController();

  (async () => {
    try {
      const response = await fetch(
        `${client.defaults.baseURL}/catalog/ontology/generate`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
          },
          body: JSON.stringify({ datasource_id: datasourceId }),
          signal: abort.signal,
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('无法读取响应流');

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = 'progress';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (currentEvent === 'progress') onEvent('progress', data);
              else if (currentEvent === 'done') onEvent('done', data);
              else if (currentEvent === 'error') onEvent('error', data);
            } catch {
              // ignore malformed data
            }
          }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') onEvent('error', { message: e.message || String(e) });
    }
  })();

  return abort;
}

export const ontologyApi = {
  list: (datasourceId?: number) =>
    client.get<{ items: OntologyModelSummary[] }>('/catalog/ontology/models', {
      params: datasourceId ? { datasource_id: datasourceId } : {},
    }),
  get: (id: number) => client.get<OntologyModel>(`/catalog/ontology/models/${id}`),
  save: (id: number, jsonContent: string, name?: string) =>
    client.put<OntologyModel>(`/catalog/ontology/models/${id}`, {
      json_content: jsonContent,
      name,
    }),
  activate: (id: number) => client.post<OntologyModel>(`/catalog/ontology/models/${id}/activate`),
  archive: (id: number) => client.post<OntologyModel>(`/catalog/ontology/models/${id}/archive`),
  remove: (id: number) => client.delete(`/catalog/ontology/models/${id}`),
  search: (q: string, datasourceId: number, limit = 5) =>
    client.get<{ items: OntologyObjectHit[] }>('/catalog/ontology/search', {
      params: { q, datasource_id: datasourceId, limit },
    }),
};
