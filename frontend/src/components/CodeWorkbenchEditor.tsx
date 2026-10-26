import { useEffect, useRef } from 'react'
import * as monaco from 'monaco-editor-core/esm/vs/editor/editor.api.js'
import EditorWorker from 'monaco-editor-core/esm/vs/editor/editor.worker.start.js?worker'

export type ScriptLanguage = 'python' | 'matlab' | 'wolfram' | 'materialsscript'

type Props = {
  language: ScriptLanguage
  value: string
  onChange: (value: string) => void
  ariaLabel: string
}

let initialized = false

function initializeEditor() {
  if (initialized) return
  initialized = true

  // Monaco is VS Code's editor core. Vendor language services and execution
  // engines remain separate; highlighting never implies a valid scientific run.
  self.MonacoEnvironment = { getWorker: () => new EditorWorker() }
  for (const id of ['python', 'matlab', 'wolfram', 'materialsscript']) monaco.languages.register({ id })
  monaco.languages.setMonarchTokensProvider('python', {
    tokenizer: { root: [
      [/\b(?:def|class|if|elif|else|for|while|return|import|from|as|with|try|except|finally|raise|True|False|None)\b/, 'keyword'],
      [/#.*$/, 'comment'], [/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/, 'string'], [/\b\d+(?:\.\d+)?\b/, 'number'],
    ] },
  })
  monaco.languages.setMonarchTokensProvider('matlab', {
    tokenizer: { root: [
      [/\b(?:function|end|if|elseif|else|for|while|switch|case|return|classdef|properties|methods)\b/, 'keyword'],
      [/%.*$/, 'comment'], [/'(?:[^']|'')*'/, 'string'], [/\b\d+(?:\.\d+)?\b/, 'number'],
    ] },
  })
  monaco.languages.setMonarchTokensProvider('wolfram', {
    tokenizer: { root: [
      [/\(\*/, { token: 'comment', next: '@comment' }],
      [/\b(?:Module|Block|With|If|Which|Table|Plot|Export|Import|Print|Set|SetDelayed)\b/, 'keyword'],
      [/"(?:[^"\\]|\\.)*"/, 'string'], [/\b\d+(?:\.\d+)?\b/, 'number'],
    ], comment: [[/\*\)/, { token: 'comment', next: '@pop' }], [/./, 'comment']] },
  })
  monaco.languages.setMonarchTokensProvider('materialsscript', {
    tokenizer: { root: [
      [/\b(?:use|my|sub|if|elsif|else|foreach|while|return|package)\b/, 'keyword'],
      [/#.*$/, 'comment'], [/"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/, 'string'], [/\b\d+(?:\.\d+)?\b/, 'number'],
    ] },
  })
  monaco.editor.defineTheme('sky-script', {
    base: 'vs', inherit: true,
    rules: [
      { token: 'keyword', foreground: '315D4D', fontStyle: 'bold' },
      { token: 'comment', foreground: '7D8980' },
      { token: 'string', foreground: '96623A' },
    ],
    colors: {
      'editor.background': '#FBF8F2', 'editor.foreground': '#30362F',
      'editorLineNumber.foreground': '#9AA39B', 'editor.selectionBackground': '#DDE8DD',
    },
  })
}

export function CodeWorkbenchEditor({ language, value, onChange, ariaLabel }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)
  const change = useRef(onChange)
  change.current = onChange

  useEffect(() => {
    if (!host.current) return
    initializeEditor()
    const instance = monaco.editor.create(host.current, {
      value, language, theme: 'sky-script', automaticLayout: true,
      fontFamily: 'Cascadia Code, Consolas, monospace', fontSize: 14,
      minimap: { enabled: false }, scrollBeyondLastLine: false,
      tabSize: 2, wordWrap: 'on', accessibilitySupport: 'auto',
      ariaLabel,
    })
    editor.current = instance
    const subscription = instance.onDidChangeModelContent(() => change.current(instance.getValue()))
    return () => { subscription.dispose(); instance.dispose(); editor.current = null }
  }, [language, ariaLabel])

  useEffect(() => {
    if (editor.current && editor.current.getValue() !== value) editor.current.setValue(value)
  }, [value])

  return <div className="warm-script-editor" ref={host} />
}
