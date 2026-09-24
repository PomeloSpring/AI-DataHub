import axios from 'axios';

const client = axios.create({ baseURL: '/api' });

// ── 错误详情归一化 ──────────────────────────────────────────────
// FastAPI 422 的 detail 是对象数组({type,loc,msg,input,ctx}[]), 全站 100+ 处
// `err.response.data.detail` 直传渲染会整页崩("Objects are not valid as a React child")。
// 在拦截器里把 detail 压成可读字符串, 所有调用点一次性变安全(含裸 axios 如 graphStore)。
function normalizeErrorDetail(data: any): any {
  if (!data || !Array.isArray(data.detail)) return data;
  data.detail = data.detail
    .map((d: any) => {
      if (typeof d === 'string') return d;
      if (d && typeof d === 'object') {
        const loc = Array.isArray(d.loc) ? d.loc.filter((x: any) => x !== 'body').join('.') : '';
        return [loc, d.msg].filter(Boolean).join(': ') || JSON.stringify(d);
      }
      return String(d);
    })
    .join('; ');
  return data;
}

axios.interceptors.response.use(
  (res) => res,
  (err) => { normalizeErrorDetail(err.response?.data); return Promise.reject(err); },
);
client.interceptors.response.use(
  (res) => res,
  (err) => { normalizeErrorDetail(err.response?.data); return Promise.reject(err); },
);

// ── Token refresh state ──────────────────────────────────────────
let isRefreshing = false;
let refreshSubscribers: ((token: string) => void)[] = [];

function subscribeTokenRefresh(cb: (token: string) => void) {
  refreshSubscribers.push(cb);
}

function onTokenRefreshed(newToken: string) {
  refreshSubscribers.forEach(cb => cb(newToken));
  refreshSubscribers = [];
}

async function doRefreshToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) return null;

  try {
    const { data } = await axios.post('/api/auth/refresh', null, {
      params: { refresh_token: refreshToken },
    });
    const newToken = data.access_token;
    const newRefresh = data.refresh_token;
    localStorage.setItem('token', newToken);
    if (newRefresh) localStorage.setItem('refresh_token', newRefresh);
    return newToken;
  } catch {
    return null;
  }
}

// ── Request interceptor: attach token ────────────────────────────
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// ── Response interceptor: auto-refresh on 401 ───────────────────
client.interceptors.response.use(
  (res) => res,
  async (err) => {
    const originalRequest = err.config;

    if (err.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      if (isRefreshing) {
        // Wait for the ongoing refresh
        return new Promise((resolve) => {
          subscribeTokenRefresh((newToken: string) => {
            originalRequest.headers.Authorization = `Bearer ${newToken}`;
            resolve(client(originalRequest));
          });
        });
      }

      isRefreshing = true;
      const newToken = await doRefreshToken();
      isRefreshing = false;

      if (newToken) {
        onTokenRefreshed(newToken);
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return client(originalRequest);
      }

      // Refresh failed — redirect to login
      localStorage.removeItem('token');
      localStorage.removeItem('refresh_token');
      window.location.href = '/login';
    }

    return Promise.reject(err);
  }
);

export default client;
