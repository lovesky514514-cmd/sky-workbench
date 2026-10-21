import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

// The visual-only routes are disabled in production. They reveal layout
// regressions at the design package's exact 1440×810 desktop viewport.
const browser = path.resolve(process.argv[2] ?? '')
const baseUrl = process.argv[3] ?? 'http://127.0.0.1:5173/'
const outputRoot = path.resolve(process.argv[4] ?? '')
if (!browser || !outputRoot) throw new Error('Usage: node warmProductVisualAudit.mjs <browser.exe> <base-url> <output-root>')
mkdirSync(outputRoot, { recursive: true })
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const freePort = () => new Promise((resolve, reject) => { const server = net.createServer(); server.once('error', reject); server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => resolve(port)) }) })

// A fresh browser profile also verifies that old persisted first-run state
// cannot influence this static design fixture.
async function capture(page, index, label) {
  const debugPort = await freePort()
  const profile = mkdtempSync(path.join(os.tmpdir(), `warm-product-${label}-`))
  const url = `${baseUrl}?warm-product-fixture=${page}`
  const child = spawn(browser, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--window-size=1440,810', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, url], { stdio: 'ignore', windowsHide: true })
  let socket
  try {
    let target
    for (let attempt = 0; attempt < 120; attempt += 1) { try { target = (await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json())).find((item) => item.type === 'page') } catch { /* Browser startup is asynchronous. */ } if (target) break; await delay(125) }
    if (!target) throw new Error(`${page}: browser target unavailable`)
    socket = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }) })
    const pending = new Map(); let nextId = 1
    socket.addEventListener('message', ({ data }) => { const message = JSON.parse(String(data)); const callbacks = pending.get(message.id); if (!callbacks) return; pending.delete(message.id); if (message.error) callbacks.reject(new Error(message.error.message)); else callbacks.resolve(message.result) })
    const command = (method, params = {}) => new Promise((resolve, reject) => { const id = nextId++; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); setTimeout(() => { if (pending.delete(id)) reject(new Error(`${page}: CDP timeout ${method}`)) }, 30000) })
    const evaluate = async (expression) => { const result = await command('Runtime.evaluate', { expression, returnByValue: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.text); return result.result.value }
    await command('Runtime.enable'); await command('Page.enable'); await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 810, deviceScaleFactor: 1, mobile: false }); await command('Page.navigate', { url })
    const selector = page === 'onboarding' ? '.warm-onboarding' : '.warm-settings'
    for (let attempt = 0; attempt < 120; attempt += 1) { if (await evaluate(`Boolean(document.querySelector(${JSON.stringify(selector)}))`).catch(() => false)) break; await delay(125) }
    if (index > 0) await evaluate(`document.querySelectorAll(${JSON.stringify(page === 'onboarding' ? '.warm-onboarding-steps button' : '.warm-settings-nav button')})[${index}].click()`)
    await delay(400)
    const metrics = JSON.parse(await evaluate(`(()=>{const box=(s)=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};return JSON.stringify({page:${JSON.stringify(page)},state:${JSON.stringify(label)},viewport:{width:innerWidth,height:innerHeight},document:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},header:box(${JSON.stringify(page === 'onboarding' ? '.warm-onboarding-header' : '.warm-settings-header')}),nav:box(${JSON.stringify(page === 'onboarding' ? '.warm-onboarding-steps' : '.warm-settings-nav')}),main:box(${JSON.stringify(page === 'onboarding' ? '.warm-onboarding-main' : '.warm-settings-main')}),title:document.querySelector('h1,h3')?.textContent,fixtureBoundary:document.body.innerText.includes('DESIGN REVIEW · NOT SCIENTIFIC DATA'),oldDashboardVisible:Boolean(document.querySelector('.instrument-workspace'))})})()`))
    metrics.gates = { exactViewport: metrics.viewport.width === 1440 && metrics.viewport.height === 810, noHorizontalOverflow: metrics.document.width <= 1440, fixtureBoundaryVisible: metrics.fixtureBoundary, visibleTitle: Boolean(metrics.title), mainDominant: metrics.main?.width > metrics.nav?.width * 2, noOldDashboard: !metrics.oldDashboardVisible }
    metrics.passed = Object.values(metrics.gates).every(Boolean)
    const screenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
    writeFileSync(path.join(outputRoot, `${label}.png`), Buffer.from(screenshot.data, 'base64'))
    return metrics
  } finally {
    if (socket?.readyState === WebSocket.OPEN) socket.close()
    if (child.exitCode === null) child.kill()
    await delay(250)
    if (profile.startsWith(path.resolve(os.tmpdir()) + path.sep)) rmSync(profile, { recursive: true, force: true })
  }
}

// Metrics and images are frozen together for a human visual decision; they do
// not imply a successful COMSOL connection or scientific validation.
const states = []
for (const [page, index, label] of [['onboarding', 0, 'onboarding'], ['onboarding', 1, 'onboarding-environment'], ['onboarding', 2, 'onboarding-local-ai'], ['onboarding', 3, 'onboarding-ready'], ['settings', 0, 'settings'], ['settings', 1, 'settings-models'], ['settings', 2, 'settings-providers'], ['settings', 3, 'settings-retrieval'], ['settings', 4, 'settings-storage']]) states.push(await capture(page, index, label))
const report = { capturedAt: new Date().toISOString(), states, allPassed: states.every((item) => item.passed) }
writeFileSync(path.join(outputRoot, 'warm_product_visual_metrics.json'), JSON.stringify(report, null, 2) + '\n')
console.log(`Warm product visual gates: ${report.allPassed ? 'PASS' : 'FAIL'} (${states.filter((item) => item.passed).length}/${states.length})`)
if (!report.allPassed) process.exitCode = 1
