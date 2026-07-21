/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      animation: {
        breath: "breath 2s ease-in-out infinite",
        "breath-dot": "breath-dot 2s ease-in-out infinite",
      },
      keyframes: {
        breath: {
          "0%, 100%": {
            boxShadow: "0 0 4px rgba(244, 143, 177, 0.3)",
            borderColor: "rgba(244, 143, 177, 0.3)",
          },
          "50%": {
            boxShadow: "0 0 18px rgba(244, 143, 177, 0.7), 0 0 36px rgba(244, 143, 177, 0.15)",
            borderColor: "rgba(244, 143, 177, 0.8)",
          },
        },
        "breath-dot": {
          "0%, 100%": {
            boxShadow: "0 0 2px rgba(244, 143, 177, 0.4)",
            backgroundColor: "rgba(244, 143, 177, 0.5)",
          },
          "50%": {
            boxShadow: "0 0 10px rgba(244, 143, 177, 0.9), 0 0 20px rgba(244, 143, 177, 0.3)",
            backgroundColor: "rgba(244, 143, 177, 1)",
          },
        },
      },
      colors: {
        sakura: {
          50: "#fff5f7",
          100: "#fce7f3",
          200: "#fbcfe0",
          300: "#f9a8d4",
          400: "#f472b6",
          500: "#ec4899",
          600: "#db2777",
          700: "#be185d",
          800: "#9d174d",
          900: "#831843",
          950: "#500724",
        },
      },
    },
  },
  plugins: [],
};
