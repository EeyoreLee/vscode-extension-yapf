const typescriptParser = require('@typescript-eslint/parser');
const typescriptPlugin = require('@typescript-eslint/eslint-plugin');

module.exports = [
    {
        ignores: ['**/out/**', '**/dist/**', '**/*.d.ts'],
    },
    {
        files: ['**/*.ts'],
        languageOptions: {
            parser: typescriptParser,
            ecmaVersion: 6,
            sourceType: 'module',
        },
        plugins: {
            '@typescript-eslint': typescriptPlugin,
        },
        rules: {
            '@typescript-eslint/naming-convention': 'warn',
            curly: 'warn',
            eqeqeq: 'warn',
            'no-throw-literal': 'warn',
            semi: 'warn',
        },
    },
];
