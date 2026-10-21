/** Render a separate presentation copy around, never instead of, verified pixels. */
export async function renderAcademicFigure(input: {
  source: string; title: string; quantity: string; unit: string
  filename: string; runId: string; originalSha256: string
}): Promise<Blob> {
  // The source URL came from the integrity-checked artifact endpoint. Drawing
  // at native pixel dimensions leaves the scientific field image unresampled.
  const image = new Image()
  image.src = input.source
  await image.decode()
  if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth > 8000 || image.naturalHeight > 7800) throw new Error('figure_size_unsupported')

  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1200, image.naturalWidth + 160)
  canvas.height = image.naturalHeight + 300
  const context = canvas.getContext('2d')
  if (!context) throw new Error('figure_export_unavailable')
  context.fillStyle = '#ffffff'
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.fillStyle = '#202a30'
  context.font = '600 29px "Segoe UI", "Microsoft YaHei", sans-serif'
  context.fillText(input.title.slice(0, 100), 80, 70, canvas.width - 160)
  context.font = '17px "Segoe UI", "Microsoft YaHei", sans-serif'
  context.fillStyle = '#53616a'
  context.fillText([input.quantity, input.unit].filter(Boolean).join(' · '), 80, 102)
  context.fillStyle = '#d7e0df'
  context.fillRect(80, 118, canvas.width - 160, 2)

  const left = Math.floor((canvas.width - image.naturalWidth) / 2)
  const imageTop = 140
  context.drawImage(image, left, imageTop)
  const footerTop = imageTop + image.naturalHeight + 28
  context.fillStyle = '#202a30'
  context.font = '16px "Segoe UI", "Microsoft YaHei", sans-serif'
  context.fillText(input.filename, 80, footerTop)
  context.fillStyle = '#53616a'
  context.font = '13px "Segoe UI", "Microsoft YaHei", sans-serif'
  context.fillText(`Run ID: ${input.runId}`, 80, footerTop + 28)
  context.fillText(`Original SHA-256: ${input.originalSha256}`, 80, footerTop + 51, canvas.width - 160)
  context.fillText('Presentation copy · original verified artifact unchanged · not independent scientific evidence', 80, footerTop + 76, canvas.width - 160)

  // The returned bytes are separate from the COMSOL artifact and its frozen
  // evidence manifest. Callers may preview or download, but never certify it.
  return new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error('figure_export_unavailable')), 'image/png'))
}

/** Save the presentation derivative without touching authoritative evidence. */
export async function exportAcademicFigure(input: Parameters<typeof renderAcademicFigure>[0]): Promise<void> {
  const blob = await renderAcademicFigure(input)
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = `${input.filename.replace(/\.png$/i, '').replace(/[^\w.-]/g, '_')}_figure_sheet.png`
    link.click()
  } finally { window.setTimeout(() => URL.revokeObjectURL(url), 1000) }
}
