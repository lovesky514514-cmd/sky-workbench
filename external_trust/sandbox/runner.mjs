// 可信宿主只开放固定内存入口。模块没有 WASI、宿主导入或通用执行接口。
import {readFileSync, readSync, writeSync} from 'node:fs';
import {createHash} from 'node:crypto';

try {
  const [mode, filename, expected] = process.argv.slice(2);
  if (!['communication', 'communication-task', 'security'].includes(mode) || !/^[a-f0-9]{64}$/.test(expected)) throw Error();
  const bytes = readFileSync(filename);
  if (bytes.length > 65536 || createHash('sha256').update(bytes).digest('hex') !== expected) throw Error();
  const module = new WebAssembly.Module(bytes);
  if (WebAssembly.Module.imports(module).length !== 0) throw Error();
  const {memory, buffer, process: evaluate, negotiate} = new WebAssembly.Instance(module, {}).exports;
  if (memory.buffer.byteLength !== 131072) throw Error();
  const data = Buffer.alloc(65537);
  let count = 0;
  while (count < data.length) {
    const n = readSync(0, data, count, data.length - count);
    if (n === 0) break;
    count += n;
  }
  const offset = buffer();
  if (count < 1 || count > 65536 || offset < 0 || offset + 65536 > memory.buffer.byteLength) throw Error();
  const view = new Uint8Array(memory.buffer, offset, 65536);
  view.set(data.subarray(0, count));
  const result = mode === 'communication-task' && count === 4 ? negotiate(...view.subarray(0, 4)) : evaluate(count);
  if (mode === 'communication-task' && count === 4 && result >= 1 && result <= 3) {
    writeSync(1, String(result));
  } else if (mode === 'communication' && result >= 0 && result <= count) {
    writeSync(1, Buffer.from(view.subarray(0, result)));
  } else if (mode === 'security' && result >= 0 && result <= 63) {
    writeSync(1, String(result));
  } else {
    throw Error();
  }
} catch {
  process.exitCode = 2;
}
