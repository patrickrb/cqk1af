import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Dark control-surface palette
        kill: "#dc2626",
        killHover: "#b91c1c",
        ok: "#16a34a",
        warn: "#eab308",
        tx: "#ef4444",
        rx: "#22c55e",
      },
    },
  },
  plugins: [],
} satisfies Config;
