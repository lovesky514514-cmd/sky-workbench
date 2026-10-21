// Render the actual existing RunBadge function, without a browser or new deps.
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
import ts from 'typescript'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
const require = createRequire(import.meta.url)
const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const translations = readFileSync(new URL('../src/i18n.ts', import.meta.url), 'utf8')
const compiledDictionary = ts.transpileModule(translations, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
const dictionary = {}
new Function('require', 'exports', compiledDictionary)(require, dictionary)
const functionSource = source.match(/^function RunBadge\(.*$/m)[0]
const active = source.match(/^const activeStates = .*$/m)[0]
const compiled = ts.transpileModule(active + '\nexport ' + functionSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
const exports = {}
new Function('require', 'exports', 'translate', compiled)(require, exports, dictionary.translate)
for (const locale of ['en-US', 'zh-CN']) {
  for (const state of ['CANCEL_REQUESTED', 'CANCELED', 'INTERRUPTED']) {
    const html = renderToStaticMarkup(React.createElement(exports.RunBadge, { state, locale }))
    assert.ok(html.includes(dictionary.translate(locale, state.replaceAll('_', ' '))))
    assert.ok(!html.includes('run-badge ready'))
    assert.equal(html.includes('run-badge working'), state === 'CANCEL_REQUESTED')
  }
}
console.log('RunBadge lifecycle: 6/6 state/locale render checks PASS (not desktop crash testing)')
