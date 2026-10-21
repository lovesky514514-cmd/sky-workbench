import assert from 'node:assert/strict'
import { readFileSync, statSync } from 'node:fs'
import { skyPetStatus } from '../src/skyPetStatus.ts'
import { petPosition } from '../src/skyPetPosition.ts'

const base = { warning:'',busy:false,askStreaming:false,pendingIntent:false,sessionState:'IDLE',scientificVerified:false }
const cases = [
  [{},'待命中'], [{busy:true},'思考中'], [{askStreaming:true},'思考中'],
  [{pendingIntent:true},'待确认'], [{sessionState:'SOLVING'},'运行中'],
  [{sessionState:'PAUSED'},'已暂停'], [{sessionState:'CANCEL_REQUESTED'},'停止中'],
  [{sessionState:'COMPLETED'},'待核验'],
  [{sessionState:'COMPLETED',scientificVerified:true},'已完成'],
  [{warning:'Runtime unavailable',busy:true},'出错了'],
  [{sessionState:'SOLVING',busy:true},'运行中'],
]
for (const [patch,wanted] of cases) {
  const actual=skyPetStatus({...base,...patch})
  assert.equal(actual,wanted)
  assert.equal([...actual].length,3)
}
const pet=readFileSync(new URL('../src/pet-main.tsx',import.meta.url),'utf8')
const studio=readFileSync(new URL('../src/WarmStudio.tsx',import.meta.url),'utf8')
const config=JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json',import.meta.url),'utf8'))
assert.ok(pet.includes('aria-live="polite"'))
assert.ok(pet.includes("emitTo('main', 'sky-pet-hidden')"))
assert.ok(pet.includes('startDragging()'))
assert.ok(!pet.includes('setInterval(') && !pet.includes('requestAnimationFrame('))
assert.ok(studio.includes('<SkyPetController') && !studio.includes('<SkyPetStatus'))
assert.equal(config.app.windows.find((window)=>window.label==='sky-pet').visible,false)
assert.equal(config.app.windows.find((window)=>window.label==='sky-pet').alwaysOnTop,true)
const area={position:{x:0,y:0},size:{width:1920,height:1040}}
assert.deepEqual(petPosition({x:120,y:120},[area],220,220),{x:120,y:120})
assert.deepEqual(petPosition({x:3000,y:120},[area],220,220),{x:1676,y:796})
assert.equal(petPosition(null,[],220,220),null)
assert.ok(statSync(new URL('../src/assets/sky-pet.png',import.meta.url)).size>1000)
console.log('SKY floating pet: 11 state cases + visibility/position/a11y contract PASS')
