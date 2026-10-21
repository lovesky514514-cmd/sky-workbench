import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import path from 'node:path'

const executable = path.resolve(process.argv[2] ?? '')
const outputRoot = path.resolve(process.argv[3] ?? '')
if (!executable || !outputRoot) throw new Error('Usage: node phase1VisualSmoke.mjs <Workbench.exe> <output-root>')

mkdirSync(outputRoot, { recursive: true })
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer()
  server.once('error', reject)
  server.listen(0, '127.0.0.1', () => {
    const { port } = server.address()
    server.close(() => resolve(port))
  })
})

const debugPort = await freePort()
const appData = path.join(outputRoot, 'runtime_state')
const workspace = path.join(appData, 'workspace')
mkdirSync(workspace, { recursive: true })
const child = spawn(executable, [], {
  cwd: path.dirname(executable),
  env: {
    ...process.env,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}`,
    COMSOL_WORKBENCH_APP_DATA_DIR: appData,
    COMSOL_WORKBENCH_WORKSPACE_DIR: workspace,
  },
  stdio: 'ignore',
  windowsHide: false,
})

let socket
const pending = new Map()
let nextId = 1

try {
  let target
  for (let attempt = 0; attempt < 480; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`desktop exited before visual inspection (${child.exitCode})`)
    try {
      const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json())
      target = targets.find((item) => item.type === 'page')
      if (target) break
    } catch { /* WebView is starting. */ }
    await delay(250)
  }
  if (!target) throw new Error('WebView2 target did not become available')

  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(String(data))
    if (!message.id || !pending.has(message.id)) return
    const callbacks = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) callbacks.reject(new Error(message.error.message)); else callbacks.resolve(message.result)
  })
  const command = (method, params = {}, timeout = 30000) => new Promise((resolve, reject) => {
    const id = nextId++
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
    setTimeout(() => { if (pending.delete(id)) reject(new Error(`CDP timeout: ${method}`)) }, timeout)
  })
  const evaluate = async (expression, timeout = 30000) => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, timeout)
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
    return result.result.value
  }
  const waitFor = async (expression, timeout = 30000) => {
    const deadline = Date.now() + timeout
    while (Date.now() < deadline) {
      try { if (await evaluate(expression)) return true } catch { /* navigation can transiently reset the document. */ }
      await delay(250)
    }
    return false
  }

  await command('Runtime.enable')
  if (!await waitFor('document.body?.innerText.includes("SKY Workbench")', 120000)) throw new Error('frontend did not load')
  await command('Page.enable', {}, 60000)
  await evaluate('localStorage.setItem("workbench-first-run-v0.5","complete");localStorage.setItem("comsol-workbench-locale","en-US");true')
  await command('Page.reload', { ignoreCache: true })
  if (!await waitFor('document.querySelector(".scientific-agent-workspace") !== null', 120000)) throw new Error('Scientific Agent Workspace did not become ready')
  await delay(1200)

  const layout = JSON.parse(await evaluate(`(()=>{
    const rectangle=(element)=>element?(({x,y,width,height})=>({x,y,width,height}))(element.getBoundingClientRect()):null;
    const tree=document.querySelector('.model-tree-panel');
    const canvas=document.querySelector('.scientific-canvas');
    const control=document.querySelector('.phase1-agent-control');
    const timeline=document.querySelector('.evidence-timeline');
    return JSON.stringify({
      viewport:{width:innerWidth,height:innerHeight,devicePixelRatio},
      tree:rectangle(tree),canvas:rectangle(canvas),control:rectangle(control),timeline:rectangle(timeline),
      taskDock:rectangle(document.querySelector('.task-dock')),
      legacyConversationMounted:Boolean(document.querySelector('.conversation-panel')),
      honestUnavailableVisible:document.body.innerText.includes('No 3D representation is generated'),
      requiredComponents:[tree,canvas,control,timeline].every(Boolean),
    });
  })()`))
  const valid = layout.requiredComponents
    && !layout.legacyConversationMounted
    && layout.tree.x < layout.canvas.x
    && layout.canvas.x < layout.control.x
    && layout.timeline.y > layout.canvas.y
    && layout.timeline.width > layout.canvas.width
  layout.visualContractPassed = valid
  if (!valid) throw new Error(`Phase 1 layout contract failed: ${JSON.stringify(layout)}`)

  const screenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
  writeFileSync(path.join(outputRoot, 'SCIENTIFIC_AGENT_WORKSPACE_PHASE1.png'), Buffer.from(screenshot.data, 'base64'))
  writeFileSync(path.join(outputRoot, 'PHASE1_UI_LAYOUT.json'), JSON.stringify(layout, null, 2) + '\n')
  console.log('Frontend Phase 1 visual smoke: PASS')
  console.log(JSON.stringify(layout))

  await evaluate(`window.__TAURI_INTERNALS__.invoke('shutdown_desktop').catch(()=>window.__TAURI_INTERNALS__.invoke('plugin:window|close',{label:'main'}).catch(()=>false))`)
  for (let attempt = 0; attempt < 60 && child.exitCode === null; attempt += 1) await delay(250)
} finally {
  if (socket?.readyState === WebSocket.OPEN) socket.close()
  if (child.exitCode === null) child.kill()
}
