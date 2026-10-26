const MAX_FIGURE_BYTES = 20_000_000

export function supportedFigureBytes(type: string, size: number, bytes: Uint8Array): boolean {
  if (size <= 0 || size > MAX_FIGURE_BYTES) return false
  if (type === 'image/png') return [137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => bytes[i] === byte)
  if (type === 'image/jpeg') return bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff
  if (type === 'image/webp') return String.fromCharCode(...bytes.slice(0, 4)) === 'RIFF' &&
    String.fromCharCode(...bytes.slice(8, 12)) === 'WEBP'
  return false
}
