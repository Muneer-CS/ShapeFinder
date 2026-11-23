export type Crop = { left: number; top: number; right: number; bottom: number }
export type LocalImage = {
  url: string
  width: number
  height: number
  dispose: () => void
}
export type TraceView = { image: LocalImage; crop: Crop; zoom: number }
export const fullCrop = (): Crop => ({ left: 0, top: 0, right: 1, bottom: 1 })
export function cropProblem(crop: Crop): string | null {
  if (Object.values(crop).some((v) => !Number.isFinite(v) || v < 0 || v > 1))
    return 'Region bounds must be finite percentages from 0 to 100.'
  if (crop.right - crop.left < 0.01 || crop.bottom - crop.top < 0.01)
    return 'Choose an ordered region at least 1% wide and 1% high.'
  return null
}
export function imageFrame(crop: Crop) {
  return {
    x: (-1000 * crop.left) / (crop.right - crop.left),
    y: (-400 * crop.top) / (crop.bottom - crop.top),
    width: 1000 / (crop.right - crop.left),
    height: 400 / (crop.bottom - crop.top),
  }
}
export async function loadLocalImage(
  file: File,
  signal: AbortSignal,
): Promise<LocalImage> {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type))
    throw new Error(
      'Use a PNG, JPEG, or WebP image. SVG and animated formats are not supported.',
    )
  if (!file.size || file.size > 10 * 1024 * 1024)
    throw new Error('Choose an image greater than 0 bytes and at most 10 MiB.')
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (signal.aborted)
    throw new DOMException('Image import cancelled.', 'AbortError')
  const tag = (offset: number, length: number) =>
    String.fromCharCode(...bytes.slice(offset, offset + length))
  const png =
    bytes.length >= 8 &&
    [137, 80, 78, 71, 13, 10, 26, 10].every((b, i) => bytes[i] === b)
  const webp = tag(0, 4) === 'RIFF' && tag(8, 4) === 'WEBP'
  const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255
  if (!(
    (file.type === 'image/png' && png) ||
    (file.type === 'image/webp' && webp) ||
    (file.type === 'image/jpeg' && jpeg)
  ))
    throw new Error(
      'The file contents do not match the selected raster format.',
    )
  const data = new DataView(bytes.buffer)
  for (
    let offset = png ? 8 : 12;
    (png || webp) && offset + 8 <= bytes.length;
  ) {
    const size = data.getUint32(png ? offset : offset + 4, !png),
      kind = tag(png ? offset + 4 : offset, 4)
    if (
      kind === 'acTL' ||
      kind === 'ANIM' ||
      kind === 'ANMF' ||
      (kind === 'VP8X' && bytes[offset + 8] & 2)
    )
      throw new Error(
        'Animated images are not supported. Use a static chart image.',
      )
    const next = offset + (png ? 12 : 8) + size + (webp ? size % 2 : 0)
    if (next > bytes.length) break
    offset = next
  }
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Image import cancelled.', 'AbortError'))
      return
    }
    const image = new Image(),
      url = URL.createObjectURL(file)
    let revoked = false
    const dispose = () => {
      if (!revoked) {
        revoked = true
        URL.revokeObjectURL(url)
      }
    }
    const cleanup = () => {
      image.onload = null
      image.onerror = null
      signal.removeEventListener('abort', abort)
      image.src = ''
    }
    const fail = (error: Error) => {
      cleanup()
      dispose()
      reject(error)
    }
    const abort = () =>
      fail(new DOMException('Image import cancelled.', 'AbortError'))
    image.onload = () => {
      const width = image.naturalWidth,
        height = image.naturalHeight
      if (
        width < 32 ||
        height < 32 ||
        width > 8192 ||
        height > 8192 ||
        width * height > 24_000_000
      ) {
        fail(
          new Error(
            'Use dimensions from 32 to 8192 pixels per side, with at most 24 million pixels.',
          ),
        )
        return
      }
      cleanup()
      resolve({ url, width, height, dispose })
    }
    image.onerror = () =>
      fail(
        new Error(
          'The image could not be decoded. Choose a valid PNG, JPEG, or WebP file.',
        ),
      )
    signal.addEventListener('abort', abort, { once: true })
    image.src = url
  })
}
