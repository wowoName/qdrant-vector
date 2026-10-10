import { defineConfig, presetUno, presetIcons } from 'unocss'

export default defineConfig({
  presets: [
    presetUno(),
    presetIcons({
      scale: 1.2,
    }),
  ],
  theme: {
    colors: {
      primary: '#2563eb',
      success: '#16a34a',
      warning: '#d97706',
      danger: '#dc2626',
      info: '#7a8699',
    },
  },
  shortcuts: {
    'flex-center': 'flex items-center justify-center',
    'flex-between': 'flex items-center justify-between',
    'card-container': 'rounded-lg p-4',
  },
})
