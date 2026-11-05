/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#05080f",
          900: "#0a0f1a",
          850: "#0e1422",
          800: "#121a2b",
          700: "#1b2538",
          600: "#26324a",
          500: "#3a4864",
          400: "#62708c",
          300: "#8d99b3",
          200: "#b9c2d6",
          100: "#e3e8f2",
        },
        up: "#22c55e",
        down: "#f43f5e",
        accent: "#38bdf8",
        warn: "#f59e0b",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        scan: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.35s ease-out both",
        scan: "scan 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
