/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      boxShadow: {
        glow: '0 20px 80px rgba(14, 165, 233, 0.16)',
      },
      colors: {
        ink: '#0f172a',
        paper: '#f8fafc',
      },
    },
  },
  plugins: [],
};
