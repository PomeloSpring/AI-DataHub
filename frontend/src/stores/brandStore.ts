import { create } from 'zustand';
import client from '../api/client';
import { DEFAULT_BRAND, setBrandSnapshot } from '../lib/brand';

export interface BrandSettings {
  app_name: string;
  logo_url: string;
  show_icon: boolean;
  show_text: boolean;
  /** 品牌标语（登录页名称下方那行）：'' = 管理员显式清空 = 不显示 */
  tagline: string;
}

interface BrandState {
  brand: BrandSettings;
  loaded: boolean;
  fetchBrand: () => Promise<void>;
  updateBrand: (updates: Partial<BrandSettings>) => Promise<BrandSettings>;
}

export const useBrandStore = create<BrandState>((set) => ({
  brand: DEFAULT_BRAND,
  loaded: false,

  fetchBrand: async () => {
    try {
      const { data } = await client.get('/admin/brand');
      const brand = { ...DEFAULT_BRAND, ...data };
      set({ brand, loaded: true });
      // 浏览器标签标题与图标只有一条落地路径：lib/brand.applyDocumentBrand
      setBrandSnapshot(brand);
    } catch (err) {
      // 拉不到就按默认品牌渲染（首屏不能空着），但必须可诊断：静默回退会让
      // “品牌改了没生效”与“接口挂了”两种事实在日志里长得一模一样
      console.warn('[brand] 品牌设置读取失败，回落默认品牌', err);
      set({ loaded: true });
    }
  },

  updateBrand: async (updates) => {
    const { data } = await client.put('/admin/brand', updates);
    const brand = { ...DEFAULT_BRAND, ...data };
    set({ brand });
    setBrandSnapshot(brand);
    return brand;
  },
}));
