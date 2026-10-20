/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: {
          DEFAULT: '#06070A',
          deep: '#040507',
        },
        surface: {
          0: '#0A0C10',
          1: '#0E1117',
          2: '#141820',
          3: '#1A1F2A',
          4: '#222836',
          hover: '#1C2230',
        },
        border: {
          subtle: 'rgba(255,255,255,0.06)',
          DEFAULT: 'rgba(255,255,255,0.09)',
          medium: 'rgba(255,255,255,0.14)',
          strong: 'rgba(255,255,255,0.22)',
        },
        ink: {
          primary: '#F4F6F9',
          secondary: '#A8B0C0',
          muted: '#6B7385',
          faint: '#4A5160',
        },
        accent: {
          DEFAULT: '#10B981',
          soft: 'rgba(16,185,129,0.12)',
          glow: 'rgba(16,185,129,0.28)',
          hover: '#34D399',
          cyan: '#06B6D4',
          cyanSoft: 'rgba(6,182,212,0.12)',
          cyanGlow: 'rgba(6,182,212,0.28)',
        },
        danger: {
          DEFAULT: '#EF4444',
          soft: 'rgba(239,68,68,0.12)',
          hover: '#F87171',
        },
        warn: {
          DEFAULT: '#F59E0B',
          soft: 'rgba(245,158,11,0.12)',
        },
      },
      fontFamily: {
        sans: [
          '"Plus Jakarta Sans"',
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
        display: ['"Plus Jakarta Sans"', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
        display: ['2.75rem', { lineHeight: '1.1', letterSpacing: '-0.02em' }],
        title: ['1.75rem', { lineHeight: '1.2', letterSpacing: '-0.015em' }],
      },
      boxShadow: {
        'elevation-1': '0 1px 2px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.03)',
        'elevation-2': '0 8px 24px -6px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.04)',
        'elevation-3': '0 20px 48px -12px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.05)',
        dock: '0 24px 64px -12px rgba(0,0,0,0.85), 0 0 0 1px rgba(255,255,255,0.08)',
        glow: '0 0 32px rgba(16,185,129,0.22)',
        'glow-cyan': '0 0 28px rgba(6,182,212,0.2)',
        'inner-glow': 'inset 0 1px 0 rgba(255,255,255,0.06)',
      },
      borderRadius: {
        '2xl': '1rem',
        '3xl': '1.25rem',
        '4xl': '1.5rem',
      },
      spacing: {
        18: '4.5rem',
        22: '5.5rem',
      },
      animation: {
        'orbit-1': 'orbitSpin 16s linear infinite',
        'orbit-2': 'orbitSpin 16s linear infinite reverse',
        'pulse-soft': 'pulseSoft 2.8s ease-in-out infinite',
        'fade-in': 'fadeIn 0.35s ease-out',
        'slide-up': 'slideUp 0.4s cubic-bezier(0.16,1,0.3,1)',
      },
      keyframes: {
        orbitSpin: {
          from: { transform: 'rotate(0deg)' },
          to: { transform: 'rotate(360deg)' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.5', transform: 'scale(1.15)' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      transitionTimingFunction: {
        smooth: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
};
