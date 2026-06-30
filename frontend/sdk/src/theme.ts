export interface ChatBITheme {
  primaryColor?: string;
  successColor?: string;
  warningColor?: string;
  errorColor?: string;
  textColor?: string;
  textSecondary?: string;
  bgColor?: string;
  surfaceColor?: string;
  borderColor?: string;
  borderRadius?: string;
  fontFamily?: string;
  fontSize?: string;
}

export const defaultTheme: Required<ChatBITheme> = {
  primaryColor: '#1677ff',
  successColor: '#52c41a',
  warningColor: '#faad14',
  errorColor: '#ff4d4f',
  textColor: '#1a1a1a',
  textSecondary: '#666666',
  bgColor: '#ffffff',
  surfaceColor: '#f5f5f5',
  borderColor: '#e0e0e0',
  borderRadius: '8px',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  fontSize: '14px',
};

export function applyTheme(host: ShadowRoot, theme?: ChatBITheme): void {
  const merged = { ...defaultTheme, ...theme };

  // Check if host page defines CSS custom properties
  const rootStyle = getComputedStyle(document.documentElement);
  const getVar = (name: string, fallback: string) =>
    rootStyle.getPropertyValue(name).trim() || fallback;

  const css = `
    :host {
      --chatbi-primary: ${getVar('--chatbi-primary', merged.primaryColor)};
      --chatbi-success: ${getVar('--chatbi-success', merged.successColor)};
      --chatbi-warning: ${getVar('--chatbi-warning', merged.warningColor)};
      --chatbi-error: ${getVar('--chatbi-error', merged.errorColor)};
      --chatbi-text: ${getVar('--chatbi-text', merged.textColor)};
      --chatbi-text-secondary: ${getVar('--chatbi-text-secondary', merged.textSecondary)};
      --chatbi-bg: ${getVar('--chatbi-bg', merged.bgColor)};
      --chatbi-surface: ${getVar('--chatbi-surface', merged.surfaceColor)};
      --chatbi-border: ${getVar('--chatbi-border', merged.borderColor)};
      --chatbi-radius: ${getVar('--chatbi-radius', merged.borderRadius)};
      --chatbi-font: ${getVar('--chatbi-font', merged.fontFamily)};
      --chatbi-font-size: ${getVar('--chatbi-font-size', merged.fontSize)};
    }
  `;

  let styleEl = host.querySelector('#chatbi-theme') as HTMLStyleElement;
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = 'chatbi-theme';
    host.prepend(styleEl);
  }
  styleEl.textContent = css;
}
