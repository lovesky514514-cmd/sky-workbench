import test from 'node:test'
import assert from 'node:assert/strict'
import { supportedFigureBytes } from '../src/localFigure.ts'

test('local preview requires a matching raster signature and a bounded file size', () => {
  assert.equal(supportedFigureBytes('image/png', 8, Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10])), true)
  assert.equal(supportedFigureBytes('image/jpeg', 3, Uint8Array.from([255, 216, 255])), true)
  assert.equal(supportedFigureBytes('image/webp', 12, new TextEncoder().encode('RIFFxxxxWEBP')), true)
  assert.equal(supportedFigureBytes('image/png', 10, new TextEncoder().encode('<script>')), false)
  assert.equal(supportedFigureBytes('image/svg+xml', 6, new TextEncoder().encode('<svg/>')), false)
  assert.equal(supportedFigureBytes('image/png', 20_000_001, Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10])), false)
})
