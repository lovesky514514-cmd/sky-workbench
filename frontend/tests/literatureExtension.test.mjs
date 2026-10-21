import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('literature navigation is hidden unless persisted settings enable it', () => {
  const shell = source('../src/WarmStudio.tsx')
  assert.match(shell, /props\.literatureEnabled && <button/)
  assert.match(shell, /homeSection === 'literature' && props\.literatureEnabled/)
  const settings = source('../src/components/WarmSettings.tsx')
  assert.match(settings, /settings\.extensions\?\.literature_modeling\?\.enabled/)
  assert.match(settings, /api\.setLiteratureModeling\(literatureEnabled\)/)
})

test('a candidate needs an explicit Yes before opening a fresh plan draft', () => {
  const review = source('../src/components/LiteratureModelingWorkspace.tsx')
  assert.match(review, /Do you want to model this candidate\?/)
  assert.match(review, /disabled=\{!candidate\.can_open_plan\}/)
  assert.match(review, /No · do not model/)
  assert.doesNotMatch(review, /api\.sendMessage|api\.resumeRun/)
  const shell = source('../src/WarmStudio.tsx')
  assert.match(shell, /props\.onNewSession\(\)\.then\(\(\) => \{ props\.onPromptChange\(excerpt\)/)
})

test('cloud extraction is independently opt-in and official-source lookup is explicit', () => {
  const settings = source('../src/components/WarmSettings.tsx')
  const review = source('../src/components/LiteratureModelingWorkspace.tsx')
  assert.match(settings, /settings\.extensions\?\.literature_modeling\?\.deepseek_enabled/)
  assert.match(settings, /api\.setLiteratureDeepSeek\(literatureDeepSeek\)/)
  assert.match(settings, /DeepSeek key detected in current Runtime/)
  assert.match(review, /api\.extractLiteratureSource\(sourceUrl\.trim\(\)\)/)
  assert.match(review, /Only official arXiv\/PMC article abstracts from the last three years/)
})
