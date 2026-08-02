import type { Config } from "tailwindcss";

export default {
  darkMode: "media",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Replace with a licensed face. Inter at default weight on everything is
      // the single most common "generated template" tell.
      fontFamily: { sans: ["var(--font-sans)", "system-ui", "sans-serif"] },
    },
  },
} satisfies Config;
