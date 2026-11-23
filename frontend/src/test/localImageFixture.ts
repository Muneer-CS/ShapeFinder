import { vi } from 'vitest'
export const pngHeader = () => new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])
export function imageFile(bytes = pngHeader(), type = 'image/png') {
  const file = new File([bytes as BlobPart], 'private-chart-name.png', { type })
  // JSDOM's File omits the modern browser arrayBuffer method.
  Object.defineProperty(file, 'arrayBuffer', {
    value: async () =>
      bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  })
  return file
}
export function mockLocalImages() {
  const images: FakeImage[] = []
  class FakeImage {
    naturalWidth = 1000
    naturalHeight = 400
    src = ''
    onload: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor() {
      images.push(this)
    }
  }
  const original = URL
  const create = vi
      .fn()
      .mockImplementation(() => `blob:local-fixture-${images.length}`),
    revoke = vi.fn()
  vi.stubGlobal(
    'URL',
    class extends original {
      static createObjectURL = create
      static revokeObjectURL = revoke
    },
  )
  vi.stubGlobal('Image', FakeImage)
  return { images, create, revoke }
}
