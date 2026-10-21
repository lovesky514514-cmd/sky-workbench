import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

// The three screenshots are visual fixtures, not scientific runs. The caller
// explicitly enables VITE_VISUAL_FIXTURES before starting the local server.
const browser = path.resolve(process.argv[2] ?? '')
const baseUrl = process.argv[3] ?? 'http://127.0.0.1:5173/'
const outputRoot = path.resolve(process.argv[4] ?? '')
if (!browser || !outputRoot) throw new Error('Usage: node warmStudioVisualAudit.mjs <browser.exe> <base-url> <output-root>')
mkdirSync(outputRoot, { recursive: true })

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer()
  server.once('error', reject)
  server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => resolve(port)) })
})

// A fresh local browser profile prevents cached page state from concealing a
// first-open failure. CDP evaluates layout and captures the exact viewport.
async function capture(page) {
  const debugPort = await freePort()
  const profile = mkdtempSync(path.join(os.tmpdir(), `warm-studio-${page}-`))
  const url = `${baseUrl}?warm-fixture=${page}`
  const child = spawn(browser, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--window-size=1440,810', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, url], { stdio: 'ignore', windowsHide: true })
  let socket
  const pending = new Map()
  let nextId = 1
  try {
    let target
    for (let attempt = 0; attempt < 120; attempt += 1) {
      try { target = (await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json())).find((item) => item.type === 'page') } catch { /* Browser startup is asynchronous. */ }
      if (target) break
      await delay(125)
    }
    if (!target) throw new Error(`${page}: browser target unavailable`)
    socket = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }) })
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(String(data))
      const callbacks = pending.get(message.id)
      if (!callbacks) return
      pending.delete(message.id)
      if (message.error) callbacks.reject(new Error(message.error.message)); else callbacks.resolve(message.result)
    })
    const command = (method, params = {}) => new Promise((resolve, reject) => {
      const id = nextId++
      pending.set(id, { resolve, reject })
      socket.send(JSON.stringify({ id, method, params }))
      setTimeout(() => { if (pending.delete(id)) reject(new Error(`${page}: CDP timeout ${method}`)) }, 30000)
    })
    const evaluate = async (expression) => {
      const result = await command('Runtime.evaluate', { expression, returnByValue: true })
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
      return result.result.value
    }
    await command('Runtime.enable')
    await command('Page.enable')
    await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 810, deviceScaleFactor: 1, mobile: false })
    await command('Page.navigate', { url })
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (await evaluate('Boolean(document.querySelector(".warm-studio"))').catch(() => false)) break
      await delay(125)
    }
    await delay(500)
    if (page === 'plan') { await evaluate("document.querySelector('.warm-plan-summary')?.scrollIntoView({block:'start'})"); await delay(180) }
    const metrics = JSON.parse(await evaluate(`(()=>{const box=(s)=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};return JSON.stringify({viewport:{width:innerWidth,height:innerHeight},document:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},rail:box('.warm-rail'),header:box('.warm-header'),main:box('.warm-main'),viewportPanel:box('.warm-viewport'),inspector:box('.warm-science-inspector'),title:document.querySelector('h1')?.textContent,fixtureBoundary:document.body.innerText.includes('DESIGN REVIEW · NOT SCIENTIFIC DATA'),oldDashboardVisible:Boolean(document.querySelector('.instrument-workspace')),page:${JSON.stringify(page)}})})()`))
    metrics.gates = { exactViewport: metrics.viewport.width === 1440 && metrics.viewport.height === 810, noHorizontalOverflow: metrics.document.width <= 1440, designBoundaryVisible: metrics.fixtureBoundary, warmHeader: metrics.header?.height === 60, warmRail: metrics.rail?.width === 200, noOldDashboard: !metrics.oldDashboardVisible, scienceSurfaceDominant: page === 'task' || page === 'plan' || metrics.viewportPanel?.width > metrics.inspector?.width * 1.8 }
    metrics.passed = Object.values(metrics.gates).every(Boolean)
    const screenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
    writeFileSync(path.join(outputRoot, `${page}.png`), Buffer.from(screenshot.data, 'base64'))
    return metrics
  } finally {
    if (socket?.readyState === WebSocket.OPEN) socket.close()
    if (child.exitCode === null) child.kill()
    await delay(500)
    // Recursive cleanup is limited to this validated, newly created temp profile.
    if (profile.startsWith(path.resolve(os.tmpdir()) + path.sep)) {
      for (let attempt = 0; attempt < 5; attempt += 1) {
        try { rmSync(profile, { recursive: true, force: true }); break }
        catch (cause) {
          if (attempt === 4) console.warn(`${page}: temporary browser profile cleanup deferred: ${cause.message}`)
          else await delay(350)
        }
      }
    }
  }
}

// Freezing metrics alongside screenshots makes the visual decision auditable.
const results = []
for (const page of ['task', 'plan', 'model', 'results']) results.push(await capture(page))
const report = { capturedAt: new Date().toISOString(), states: results, allPassed: results.every((item) => item.passed) }
writeFileSync(path.join(outputRoot, 'warm_studio_visual_metrics.json'), JSON.stringify(report, null, 2) + '\n')
console.log(`Warm Studio visual gates: ${report.allPassed ? 'PASS' : 'FAIL'} (${results.filter((item) => item.passed).length}/${results.length})`)
if (!report.allPassed) process.exitCode = 1
