import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { DatabaseSync } from 'node:sqlite'
import net from 'node:net'
import path from 'node:path'

// This is a startup and UI contract smoke test, not a COMSOL solve or an AI
// inference benchmark. Its app-data directory is isolated under the output.
const executable = path.resolve(process.argv[2] ?? '')
const outputRoot = path.resolve(process.argv[3] ?? '')
const useDefaultProfile = process.argv.includes('--default-profile')
const seedCorrupt = process.argv.includes('--seed-corrupt')
const seedOldSchema = process.argv.includes('--seed-old-schema')
if (!executable || !outputRoot) throw new Error('Usage: node warmStudioPackagedSmoke.mjs <Workbench.exe> <output-root>')
mkdirSync(outputRoot, { recursive: true })
const appData = path.join(outputRoot, 'runtime_state')
const workspace = path.join(appData, 'workspace')
mkdirSync(workspace, { recursive: true })
// These fixtures are confined to the test profile. No real user DB is read or
// replaced, and the original fixture bytes must survive Runtime startup.
if (seedCorrupt || seedOldSchema) {
  const database = path.join(appData, 'sessions.db')
  if (seedCorrupt) writeFileSync(database, Buffer.from('truncated-not-sqlite'))
  else {
    const old = new DatabaseSync(database)
    old.exec("CREATE TABLE sessions(id TEXT, title TEXT, state TEXT); INSERT INTO sessions VALUES('old','Legacy claim','COMPLETED');")
    old.close()
  }
}
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer()
  server.once('error', reject)
  server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => resolve(port)) })
})
const alive = (pid) => { try { process.kill(pid, 0); return true } catch { return false } }
const pidForPort = (port) => {
  const output = spawnSync('netstat.exe', ['-ano', '-p', 'tcp'], { encoding: 'utf8', windowsHide: true }).stdout ?? ''
  const line = output.split(/\r?\n/).find((item) => item.includes(`127.0.0.1:${port}`) && item.includes('LISTENING'))
  return Number(line?.trim().split(/\s+/).at(-1) ?? 0)
}

// Attach to the real packaged WebView2, not Vite and not a fixture route.
const debugPort = await freePort()
const launchEnv = { ...process.env, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${debugPort}` }
if (useDefaultProfile) {
  // This opt-in path validates a repaired real user profile. Remove inherited
  // overrides so a passing isolated-profile check cannot masquerade as it.
  delete launchEnv.COMSOL_WORKBENCH_APP_DATA_DIR
  delete launchEnv.COMSOL_WORKBENCH_WORKSPACE_DIR
} else {
  launchEnv.COMSOL_WORKBENCH_APP_DATA_DIR = appData
  launchEnv.COMSOL_WORKBENCH_WORKSPACE_DIR = workspace
}
const child = spawn(executable, [], { cwd: path.dirname(executable), windowsHide: true, stdio: 'ignore', env: launchEnv })
let socket
const pending = new Map()
let nextId = 1
let runtimePid = 0
let evaluateForCleanup = null
let storageBefore = null
let report = { passed: false, reason: 'not completed' }
try {
  let target
  for (let attempt = 0; attempt < 480; attempt += 1) {
    try { target = (await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((response) => response.json())).find((item) => item.type === 'page') } catch { /* WebView2 startup is asynchronous. */ }
    if (target) break
    await delay(250)
  }
  if (!target) throw new Error('Packaged WebView2 target unavailable')
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
    setTimeout(() => { if (pending.delete(id)) reject(new Error(`CDP timeout ${method}`)) }, 30000)
  })
  const evaluate = async (expression) => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
    return result.result.value
  }
  evaluateForCleanup = evaluate
  const waitFor = async (expression, maxMs) => {
    for (let elapsed = 0; elapsed < maxMs; elapsed += 250) {
      if (await evaluate(expression).catch(() => false)) return true
      await delay(250)
    }
    return false
  }
  await command('Runtime.enable')
  await command('Page.enable')
  if (!await waitFor('Boolean(document.querySelector(".warm-studio"))', 120000)) throw new Error('WarmStudio did not mount in packaged app')
  storageBefore = JSON.parse(await evaluate('JSON.stringify({guide:localStorage.getItem("warm-studio-onboarding-v2"),legacy:localStorage.getItem("workbench-first-run-rc41"),locale:localStorage.getItem("comsol-workbench-locale")})'))
  // A legacy completion flag must not suppress the new warm first-run guide.
  await evaluate('localStorage.setItem("workbench-first-run-rc41","complete");localStorage.removeItem("warm-studio-onboarding-v2");localStorage.setItem("comsol-workbench-locale","en-US");true')
  await command('Page.reload', { ignoreCache: true })
  const firstRunVisible = await waitFor('Boolean(document.querySelector(".warm-onboarding"))', 30000)
  if (!firstRunVisible) throw new Error('Warm onboarding did not appear at first launch')
  const onboardingScreenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
  writeFileSync(path.join(outputRoot, 'packaged-onboarding.png'), Buffer.from(onboardingScreenshot.data, 'base64'))
  await evaluate('document.querySelector(".warm-onboarding-header > button")?.click();true')
  if (!await waitFor('!document.querySelector(".warm-onboarding") && Boolean(document.querySelector(".warm-task-page"))', 120000)) throw new Error('Task page did not load after first-run dismissal')
  const onboardingStored = await evaluate('localStorage.getItem("warm-studio-onboarding-v2")==="complete"')
  const bootstrap = JSON.parse(await evaluate('(async()=>JSON.stringify(await window.__TAURI_INTERNALS__.invoke("desktop_bootstrap")))()'))
  // React mounts before the owned sidecar has finished bootstrapping. Poll
  // health rather than turning normal startup latency into a false failure.
  const healthExpression = `fetch(${JSON.stringify(bootstrap.baseUrl + '/api/health')},{headers:{'X-Workbench-Token':${JSON.stringify(bootstrap.sessionToken)}}}).then(r=>r.ok).catch(()=>false)`
  const health = await waitFor(healthExpression, 60000)
  runtimePid = pidForPort(Number(new URL(bootstrap.baseUrl).port))
  if (!health) throw new Error(`Owned scientific runtime health check failed at ${bootstrap.baseUrl}`)
  const recoveryHealth = await fetch(bootstrap.baseUrl + '/api/health', { headers: { 'X-Workbench-Token': bootstrap.sessionToken } }).then((response) => response.json())
  const quarantined = seedCorrupt || seedOldSchema
  const quarantineVisible = quarantined && await waitFor('Boolean(document.querySelector(".warm-recovery-banner")?.textContent?.includes("unverified"))', 10000)
  if (quarantined && (recoveryHealth.database_recovery?.status !== 'history_quarantined' || !quarantineVisible)) {
    throw new Error('Packaged database quarantine status or visible warning is missing')
  }
  let recoveredSessions = null
  let recoveryBanner = null
  if (useDefaultProfile) {
    // Check the actual Runtime projection: recovered tasks must remain
    // interrupted and must not carry an old scientific model/result verdict.
    const response = await fetch(bootstrap.baseUrl + '/api/sessions', { headers: { 'X-Workbench-Token': bootstrap.sessionToken } })
    if (!response.ok) throw new Error(`Default-profile session API failed: ${response.status}`)
    const data = await response.json()
    recoveredSessions = data.sessions?.length ?? -1
    if (recoveredSessions !== 3 || data.sessions.some((item) => item.state !== 'INTERRUPTED' || item.model !== null || !item.error?.includes('incomplete'))) {
      throw new Error('Recovered default profile did not retain its interrupted/unverified contract')
    }
    recoveryBanner = await waitFor('Boolean(document.querySelector(".warm-recovery-banner")?.textContent?.includes("unverified"))', 10000)
    if (!recoveryBanner) throw new Error('Recovered history warning is not visible in the packaged workspace')
  }
  const task = await evaluate('Boolean(document.querySelector(".warm-task-focus textarea"))')
  const settingsClick = await evaluate('(()=>{const e=document.querySelector(".warm-rail-settings");if(!e)return false;e.click();return true})()')
  const warmSettings = settingsClick && await waitFor('Boolean(document.querySelector(".warm-settings"))', 10000)
  const settingsSections = await evaluate('document.querySelectorAll(".warm-settings-nav button").length')
  const settingsScreenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
  writeFileSync(path.join(outputRoot, 'packaged-settings.png'), Buffer.from(settingsScreenshot.data, 'base64'))
  await evaluate('document.querySelector(".warm-settings-header > button")?.click();true')
  const clicked = await evaluate('(()=>{const e=[...document.querySelectorAll(".warm-tabs button")].find(x=>x.textContent?.trim()==="Results");if(!e)return false;e.click();return true})()')
  if (!clicked || !await waitFor('Boolean(document.querySelector(".warm-result-viewport"))', 10000)) throw new Error('Results tab did not open')
  const honestEmpty = await evaluate('document.querySelector(".warm-result-viewport")?.innerText.includes("No verified result artifact available")')
  const noFixture = await evaluate('!document.body.innerText.includes("DESIGN REVIEW · NOT SCIENTIFIC DATA")')
  const resultsScreenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
  writeFileSync(path.join(outputRoot, 'packaged-results.png'), Buffer.from(resultsScreenshot.data, 'base64'))
  const languageClick = await evaluate('(()=>{const e=document.querySelector(".warm-language");if(!e)return false;e.click();return true})()')
  const languageStored = await evaluate('localStorage.getItem("comsol-workbench-locale")==="zh-CN"')
  await command('Page.reload', { ignoreCache: true })
  const languageRestored = await waitFor('document.documentElement.lang==="zh-CN" && Boolean(document.querySelector(".warm-studio"))', 30000)
  if (useDefaultProfile && !await waitFor('Boolean(document.querySelector(".warm-recovery-banner"))', 30000)) {
    throw new Error('Recovery banner disappeared after packaged language reload')
  }
  const screenshot = await command('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false })
  writeFileSync(path.join(outputRoot, 'packaged-frontend.png'), Buffer.from(screenshot.data, 'base64'))
  report = { passed: Boolean(firstRunVisible && onboardingStored && warmSettings && settingsSections >= 5 && task && honestEmpty && noFixture && languageClick && languageStored && languageRestored && runtimePid > 0 && (!useDefaultProfile || (recoveredSessions === 3 && recoveryBanner)) && (!quarantined || quarantineVisible)), health, firstRunVisible, onboardingStored, warmSettings, settingsSections, task, honestEmpty, noFixture, languageStored, languageRestored, recoveredSessions, recoveryBanner, quarantineVisible, recoveryStatus: recoveryHealth.database_recovery?.status, runtimePid, selectedModel: bootstrap.selectedModel, publicProfile: true, profile: seedCorrupt ? 'isolated_corrupt' : seedOldSchema ? 'isolated_old_schema' : useDefaultProfile ? 'default_recovered' : 'isolated' }
  // The smoke test must not consume a real user's first-run experience or
  // change their language preference on this shared WebView2 origin.
  await evaluate(`(()=>{const prior=${JSON.stringify(storageBefore)};for(const [key,value] of [["warm-studio-onboarding-v2",prior.guide],["workbench-first-run-rc41",prior.legacy],["comsol-workbench-locale",prior.locale]]){if(value==null)localStorage.removeItem(key);else localStorage.setItem(key,value)}return true})()`)
  storageBefore = null
  await evaluate('window.__TAURI_INTERNALS__.invoke("shutdown_desktop").then(()=>true).catch(()=>false)')
} catch (cause) {
  report = { passed: false, reason: cause instanceof Error ? cause.message : String(cause), runtimePid }
} finally {
  if (storageBefore && evaluateForCleanup) {
    try { await evaluateForCleanup(`(()=>{const prior=${JSON.stringify(storageBefore)};for(const [key,value] of [["warm-studio-onboarding-v2",prior.guide],["workbench-first-run-rc41",prior.legacy],["comsol-workbench-locale",prior.locale]]){if(value==null)localStorage.removeItem(key);else localStorage.setItem(key,value)}return true})()`) } catch { /* The WebView may already have exited. */ }
  }
  if (socket?.readyState === WebSocket.OPEN) socket.close()
  for (let attempt = 0; attempt < 40 && alive(child.pid); attempt += 1) await delay(250)
  if (alive(child.pid)) child.kill()
  await delay(1500)
  report = { ...report, desktopExited: !alive(child.pid), runtimeOrphaned: runtimePid > 0 && alive(runtimePid) }
  report.passed = report.passed && report.desktopExited && !report.runtimeOrphaned
  writeFileSync(path.join(outputRoot, 'packaged-smoke.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(`Warm Studio packaged smoke: ${report.passed ? 'PASS' : 'FAIL'} ${JSON.stringify(report)}`)
  if (!report.passed) process.exitCode = 1
}
