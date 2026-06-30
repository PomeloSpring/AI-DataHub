import { create } from 'zustand';
import client from '../api/client';

export interface BrandSettings {
  app_name: string;
  logo_url: string;
  show_icon: boolean;
  show_text: boolean;
}

interface BrandState {
  brand: BrandSettings;
  loaded: boolean;
  fetchBrand: () => Promise<void>;
  updateBrand: (updates: Partial<BrandSettings>) => Promise<void>;
}

const DEFAULT_BRAND: BrandSettings = {
  app_name: 'AI-DataHub',
  logo_url: '',
  show_icon: true,
  show_text: true,
};

export const useBrandStore = create<BrandState>((set) => ({
  brand: DEFAULT_BRAND,
  loaded: false,

  fetchBrand: async () => {
    try {
      const { data } = await client.get('/admin/brand');
      set({ brand: { ...DEFAULT_BRAND, ...data }, loaded: true });
      // Update document title and favicon
      _applyBrand(data);
    } catch {
      set({ loaded: true });
    }
  },

  updateBrand: async (updates) => {
    const { data } = await client.put('/admin/brand', updates);
    set({ brand: { ...DEFAULT_BRAND, ...data } });
    _applyBrand(data);
  },
}));

/** Apply brand settings to document title and favicon */
function _applyBrand(brand: Partial<BrandSettings>) {
  const name = brand.app_name || 'AI-DataHub';
  document.title = `${name} - 数据分析平台`;

  // Update or reset favicon
  if (brand.logo_url) {
    _setFavicon(brand.logo_url);
  } else {
    _setFavicon('/vite.svg');
  }
}

function _setFavicon(url: string) {
  let link = document.querySelector("link[rel~='icon']") as HTMLLinkElement;
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href = url;
}
