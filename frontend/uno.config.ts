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
      primary: '#3d7dff',
      success: '#2fd39f',
      warning: '#f0b24a',
      danger: '#ff6b6b',
      info: '#8a97b4',
    },
  },
  shortcuts: {
    'flex-center': 'flex items-center justify-center',
    'flex-between': 'flex items-center justify-between',
    'card-container': 'rounded-lg p-4',
  },
})
