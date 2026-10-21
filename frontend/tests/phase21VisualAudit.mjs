import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

const browser = path.resolve(process.argv[2] ?? '')
const baseUrl = process.argv[3] ?? 'http://127.0.0.1:4173/'
const outputRoot = path.resolve(process.argv[4] ?? '')
if (!browser || !outputRoot) throw new Error('Usage: node phase21VisualAudit.mjs <browser.exe> <base-url> <output-root>')

mkdirSync(outputRoot, { recursive: true })
const states = [
  ['idle', '01_idle.png'],
  ['plan-review', '02_plan_review.png'],
  ['running', '03_running.png'],
  ['need-review', '04_need_review.png'],
  ['verified-result', '05_verified_result.png'],
  ['left-rail-expanded', '06_left_rail_expanded.png'],
  ['inspector-object-edit', '07_inspector_object_edit.png'],
]
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer()
  server.once('error', reject)
  server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => resolve(port)) })
})

async function capture(state, filename) {
  const debugPort = await freePort()
  const profile = mkdtempSync(path.join(os.tmpdir(), `comsol-phase21-${state}-`))
  const url = `${baseUrl}?phase2-fixture=${state}`
  const child = spawn(browser, [
    '--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
    '--window-size=1440,810', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, url,
  ], { stdio: 'ignore', windowsHide: true })
  let socket
  const pending = new Map()
  let nextId = 1
  try {
    let target
    for (let attempt = 0; attempt < 160; attempt += 1) {
      try {
        const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json())
        target = targets.find((item) => item.type === 'page' && item.url.includes('phase2-fixture')) ?? targets.find((item) => item.type === 'page')
        if (target) break
      } catch { /* Browser startup. */ }
      await delay(125)
    }
    if (!target) throw new Error(`${state}: browser target unavailable`)
    socket = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }) })
    socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(String(data))
      if (!message.id || !pending.has(message.id)) return
      const callbacks = pending.get(message.id); pending.delete(message.id)
      if (message.error) callbacks.reject(new Error(message.error.message)); else callbacks.resolve(message.result)
    })
    const command = (method, params = {}, timeout = 30000) => new Promise((resolve, reject) => {
      const id = nextId++; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params }))
      setTimeout(() => { if (pending.delete(id)) reject(new Error(`${state}: CDP timeout ${method}`)) }, timeout)
    })
    const evaluate = async (expression) => {
      const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
      return result.result.value
    }
    await command('Runtime.enable')
    await command('Page.enable')
    await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 810, deviceScaleFactor: 1, mobile: false })
    await command('Page.navigate', { url })
    for (let attempt = 0; attempt < 160; attempt += 1) {
      if (await evaluate('Boolean(document.querySelector(".instrument-workspace"))').catch(() => false)) break
      await delay(125)
    }
    await delay(500)
    const metrics = JSON.parse(await evaluate(`(()=>{
      const rect=(selector)=>{const element=document.querySelector(selector);if(!element)return null;const value=element.getBoundingClientRect();return {x:value.x,y:value.y,width:value.width,height:value.height}};
      const visible=(element)=>{const box=element.getBoundingClientRect();const style=getComputedStyle(element);return box.width>0&&box.height>0&&style.display!=='none'&&style.visibility!=='hidden'&&Number(style.opacity)>0};
      const textElements=[...document.querySelectorAll('.phase21-product-bar *, .instrument-workspace *')].filter((element)=>visible(element)&&[...element.childNodes].some((node)=>node.nodeType===Node.TEXT_NODE&&node.textContent.trim()));
      const fontSamples=textElements.map((element)=>({selector:element.className||element.tagName,text:element.textContent.trim().slice(0,60),px:Number.parseFloat(getComputedStyle(element).fontSize)})).filter((item)=>Number.isFinite(item.px));
      const canvas=rect('.instrument-canvas');const rail=rect('.instrument-rail');const inspector=rect('.context-inspector');const strip=rect('.execution-strip');const dock=rect('.task-dock');const brand=rect('.workbench-brand-icon');const navigation=rect('.global-mode-switch');
      return JSON.stringify({state:${JSON.stringify(state)},viewport:{width:innerWidth,height:innerHeight},document:{scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight},rail,canvas,inspector,strip,dock,brand,navigation,canvasWidthShare:canvas?canvas.width/innerWidth:0,minimumVisibleFontPx:fontSamples.length?Math.min(...fontSamples.map((item)=>item.px)):0,smallFontSamples:fontSamples.filter((item)=>item.px<11.5).slice(0,12),legacyBrandMounted:Boolean(document.querySelector('.brand-mark')),legacyConversationMounted:Boolean(document.querySelector('.conversation-panel')),fixtureBoundaryVisible:document.body.innerText.includes('VISUAL QA FIXTURE · NOT SCIENTIFIC DATA'),primarySurfaceVisible:Boolean(document.querySelector('.scientific-plane')),verified2dVisible:Boolean(document.querySelector('.verified-2d-viewport')),rectangleReadbackVisible:Boolean(document.querySelector('.rectangle-readback'))});
    })()`))
    const expectedInspector = ['plan-review', 'need-review', 'inspector-object-edit'].includes(state)
    const expectedExpandedRail = ['plan-review', 'left-rail-expanded'].includes(state)
    const expected2d = ['need-review', 'left-rail-expanded', 'inspector-object-edit'].includes(state)
    metrics.gates = {
      exactViewport: metrics.viewport.width === 1440 && metrics.viewport.height === 810,
      noHorizontalOverflow: metrics.document.scrollWidth <= metrics.viewport.width,
      brandVisible: metrics.brand?.width >= 28 && metrics.brand?.width <= 34,
      noLegacyBrand: !metrics.legacyBrandMounted,
      globalNavigationVisible: metrics.navigation?.width > 300,
      railWidth: expectedExpandedRail ? metrics.rail?.width >= 220 && metrics.rail?.width <= 245 : metrics.rail?.width <= 72,
      inspectorState: expectedInspector ? metrics.inspector?.width >= 290 && metrics.inspector?.width <= 320 : metrics.inspector == null,
      compactExecutionStrip: metrics.strip?.height <= 52,
      canvasDominant: metrics.canvasWidthShare >= 0.55,
      taskDockSubordinate: !metrics.dock || metrics.dock.width < metrics.canvas.width,
      readableTypography: metrics.minimumVisibleFontPx >= 11.5,
      noLegacyConversation: !metrics.legacyConversationMounted,
      honestFixtureBoundary: metrics.fixtureBoundaryVisible,
      primarySurfaceVisible: metrics.primarySurfaceVisible,
      verified2dState: expected2d ? metrics.verified2dVisible && metrics.rectangleReadbackVisible : true,
    }
    metrics.passed = Object.values(metrics.gates).every(Boolean)
    const screenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
    writeFileSync(path.join(outputRoot, filename), Buffer.from(screenshot.data, 'base64'))
    return metrics
  } finally {
    if (socket?.readyState === WebSocket.OPEN) socket.close()
    if (child.exitCode === null) child.kill()
    await delay(300)
    rmSync(profile, { recursive: true, force: true })
  }
}

const results = []
for (const [state, filename] of states) results.push(await capture(state, filename))
const report = { capturedAt: new Date().toISOString(), states: results, allPassed: results.every((item) => item.passed) }
writeFileSync(path.join(outputRoot, 'PHASE21_VISUAL_METRICS.json'), JSON.stringify(report, null, 2) + '\n')
console.log(`Phase 2.1 visual gates: ${report.allPassed ? 'PASS' : 'FAIL'} (${results.filter((item) => item.passed).length}/${results.length})`)
for (const item of results) console.log(`${item.state}: canvas=${(item.canvasWidthShare * 100).toFixed(1)}% rail=${item.rail?.width ?? 0}px inspector=${item.inspector?.width ?? 0}px font>=${item.minimumVisibleFontPx}px ${item.passed ? 'PASS' : 'FAIL'}`)
if (!report.allPassed) process.exitCode = 1
