import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
  build: {
    lib: {
      entry: path.resolve(__dirname, 'src/index.ts'),
      name: 'ChatBISDK',
      fileName: 'chatbi-sdk',
      formats: ['umd'],
    },
    outDir: 'dist',
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        entryFileNames: 'chatbi-sdk.min.js',
        assetFileNames: 'chatbi-sdk.[ext]',
      },
    },
  },
  define: {
    'process.env': '{}',
  },
});
