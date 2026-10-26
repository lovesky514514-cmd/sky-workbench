// A preview is allowed only when the browser confirms that the user chose one
// window. An unidentified surface could be the whole desktop or a browser tab.
export function captureIsWindowOnly(settings: { displaySurface?: string }): boolean {
  return settings.displaySurface === 'window'
}

export function captureStatus(errorName: string): 'not-allowed' | 'unavailable' {
  return errorName === 'NotAllowedError' ? 'not-allowed' : 'unavailable'
}

// A selected surface can contain more than one track. Always release all of
// them when the user stops, navigates away, or the selection is rejected.
export function stopCapture(stream: Pick<MediaStream, 'getTracks'> | null): void {
  stream?.getTracks().forEach((track) => track.stop())
}
