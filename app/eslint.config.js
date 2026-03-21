import { svelte } from "@sveltejs/vite-plugin-svelte";
import js from "@eslint/js";
import globals from "globals";

export default [
  // Base JS recommended rules
  js.configs.recommended,

  // JS / Svelte files
  {
    files: ["**/*.js", "**/*.svelte"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.es2021,
      },
    },
    plugins: {
      svelte,
    },
    rules: {
      // Errors
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "no-console": "off",           // console is fine in a dev tool
      "no-undef": "error",

      // Style
      "eqeqeq": ["error", "always"],
      "prefer-const": "warn",
      "no-var": "error",

      // Svelte-specific
      "svelte/no-unused-svelte-ignore": "warn",
      "svelte/valid-compile": "error",
    },
  },

  // Ignore build output and dependencies
  {
    ignores: ["dist/**", "node_modules/**", "src-tauri/**"],
  },
];
