import type { Config } from 'tailwindcss';

/**
 * 색상은 globals.css 의 CSS 변수를 참조한다.
 * 다크모드는 prefers-color-scheme 로 자동 전환되므로 darkMode 클래스 전략을 쓰지 않는다.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        leaf: {
          deep: '#2D5A27',
          DEFAULT: '#4CAF50',
          light: '#E8F5E9',
        },
        soil: {
          DEFAULT: '#8B6914',
          light: '#F5E6D3',
        },
        canvas: 'var(--canvas)',
        surface: 'var(--surface)',
        'surface-alt': 'var(--surface-alt)',
        ink: 'var(--ink)',
        'ink-muted': 'var(--ink-muted)',
        edge: 'var(--edge)',
        bubble: 'var(--bubble-user)',
      },
      fontFamily: {
        sans: ['var(--font-sans)'],
      },
      keyframes: {
        'leaf-bounce': {
          '0%, 80%, 100%': { transform: 'translateY(0)', opacity: '0.4' },
          '40%': { transform: 'translateY(-6px)', opacity: '1' },
        },
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'toast-in': {
          from: { opacity: '0', transform: 'translateY(12px) scale(0.97)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
      },
      animation: {
        'leaf-bounce': 'leaf-bounce 1.2s ease-in-out infinite',
        'fade-up': 'fade-up 0.28s ease-out',
        'toast-in': 'toast-in 0.22s ease-out',
      },
    },
  },
  plugins: [],
};

export default config;
