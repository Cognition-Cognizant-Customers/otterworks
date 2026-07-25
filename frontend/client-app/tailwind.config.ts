import type { Config } from "tailwindcss";

// OtterWorks classic-enterprise theme: navy/steel primary, flat square corners,
// muted amber accent, system sans stack.
const config: Config = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    borderRadius: {
      none: "0",
      sm: "1px",
      DEFAULT: "2px",
      md: "2px",
      lg: "2px",
      xl: "2px",
      "2xl": "2px",
      "3xl": "2px",
      full: "9999px",
    },
    extend: {
      colors: {
        otter: {
          50: "#f4f5f7",
          100: "#e7eaef",
          200: "#c8d1dd",
          300: "#a1b0c4",
          400: "#6d84a4",
          500: "#3f5a82",
          600: "#1f3a5f",
          700: "#1a3252",
          800: "#152a45",
          900: "#102138",
        },
        accent: {
          400: "#d99a3d",
          500: "#c9862b",
          600: "#a96e1f",
        },
      },
      fontFamily: {
        sans: ['"Segoe UI"', "Helvetica", "Arial", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
