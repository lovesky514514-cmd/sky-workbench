export type PetPosition = { x: number; y: number }
export type PetWorkArea = { position: PetPosition; size: { width: number; height: number } }

// A saved position is usable only if the entire floating window still fits a
// current monitor's work area. Otherwise place it safely above the taskbar.
export function petPosition(saved: PetPosition | null, areas: PetWorkArea[], width: number, height: number): PetPosition | null {
  const valid = (value: PetPosition, area: PetWorkArea) =>
    Number.isFinite(value.x) && Number.isFinite(value.y) &&
    value.x >= area.position.x && value.y >= area.position.y &&
    value.x + width <= area.position.x + area.size.width &&
    value.y + height <= area.position.y + area.size.height
  if (saved && areas.some((area) => valid(saved, area))) return saved
  const area = areas[0]
  if (!area) return null
  return {
    x: Math.round(area.position.x + Math.max(0, area.size.width - width - 24)),
    y: Math.round(area.position.y + Math.max(0, area.size.height - height - 24)),
  }
}
