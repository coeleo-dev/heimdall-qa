import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// The client's lint opinion, separate from the Python side's ruff config: they are two
// languages and two toolchains, and a single root config would have to pretend
// otherwise. `npm run lint` in this workspace is what CI runs.
export default tseslint.config(
  {
    ignores: [
      // The build output is committed but is not source — it is `tsc`'s and Vite's
      // result, and linting it would be reviewing a compiler's formatting choices.
      "../../src/heimdall_qa/desktop/webapp/**",
      "node_modules/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],

      // Off, deliberately, and with the reason written down rather than a bare silence.
      //
      // `set-state-in-effect` reads every synchronous `setState` in an effect as a
      // cascading render. That is good advice for a component deriving one piece of
      // state from another, and wrong for this client, whose single job is to mirror
      // an external system: `useHarness` fetches the bootstrap on mount and opens the
      // SSE stream, and any pane has to reset its own answer when the engine moves on
      // (`StepPane` clears the verdict comment, `UnitCard` follows the plan's mode).
      // Those are the two things the React docs say an effect is *for* — "synchronize
      // with an external system" and "fetch on mount". The alternative the rule pushes
      // toward, deferring the call behind a timer to dodge the check, would be lint
      // theatre around a real fetch.
      //
      // The other compiler-era rules stay on: `refs` and `purity` caught two genuine
      // bugs in this workspace (a ref written during render, and `Date.now()` called
      // in a render body), and both are fixed rather than silenced.
      "react-hooks/set-state-in-effect": "off",
    },
  },
);
