import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Unused names are an error, but an underscore prefix is the established
      // way to say a binding is deliberately discarded.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
  {
    files: ["**/*.{test,spec}.{ts,tsx}", "e2e/**/*.ts", "tests/**/*.ts"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },
  {
    files: ["*.config.{ts,js}", "vite.config.ts", "playwright.config.ts"],
    languageOptions: { globals: globals.node },
  },
  {
    // The end-to-end launcher and the dev-server runner are plain Node, so
    // `tsc -b` never sees them. Linting is the only check they get; do not
    // leave them unchecked.
    files: ["tests/e2e/**/*.mjs", "scripts/**/*.mjs"],
    extends: [js.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.node,
    },
  },
);
