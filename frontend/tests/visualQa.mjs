import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'

// Run against Vite's explicitly watermarked visual fixtures. This checks UI
// rendering and canvas export; it is not a COMSOL or scientific verdict test.
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const output = path.resolve(process.argv[2] || 'visual-qa-output')
await mkdir(output, { recursive: true })

const browser = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 1, acceptDownloads: false })

  // The generated raster is intentionally illustrative. Confirm the exported
  // sheet has a white stage, original field pixels and a visible footer.
  await page.goto('http://127.0.0.1:5177/tests/academicFigure.visual.html', { waitUntil: 'networkidle' })
  await page.waitForFunction(() => document.title.startsWith('Figure visual QA ·'))
  const figure = await page.locator('img').evaluate((image) => ({ width: image.naturalWidth, height: image.naturalHeight, complete: image.complete }))
  assert.deepEqual(figure, { width: 1200, height: 800, complete: true })
  await page.screenshot({ path: path.join(output, 'preview_figure_sheet_illustrative.png'), fullPage: true })

  // These two product-code layouts are fixture-only and visibly watermarked.
  // They let a reviewer preview the workspace without inventing a solved run.
  for (const [name, marker] of [['task', 'Create a scientific task'], ['results', 'Verified scientific result']]) {
    await page.goto(`http://127.0.0.1:5177/?warm-fixture=${name}`, { waitUntil: 'networkidle' })
    await page.getByText('DESIGN REVIEW · NOT SCIENTIFIC DATA').waitFor()
    await page.getByText(marker, { exact: false }).first().waitFor()
    await page.screenshot({ path: path.join(output, `preview_workbench_${name}_illustrative.png`) })
  }
  console.log(`VISUAL_QA_PASS ${output}`)
} finally {
  await browser.close()
}
