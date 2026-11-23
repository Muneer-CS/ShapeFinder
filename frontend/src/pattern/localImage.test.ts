import { afterEach, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'
import { cropProblem, imageFrame, loadLocalImage } from './localImage'
import {
  imageFile,
  mockLocalImages,
  pngHeader,
} from '../test/localImageFixture'
afterEach(() => vi.unstubAllGlobals())
it('maps a known selected image region into the shared editor rectangle', () => {
  expect(imageFrame({ left: 0.1, right: 0.9, top: 0.2, bottom: 0.8 })).toEqual({
    x: -125,
    width: 1250,
    y: (-400 * 0.2) / (0.8 - 0.2),
    height: 400 / (0.8 - 0.2),
  })
  expect(cropProblem({ left: 0.5, right: 0.4, top: 0, bottom: 1 })).toContain(
    'ordered',
  )
  expect(cropProblem({ left: 0, right: NaN, top: 0, bottom: 1 })).toContain(
    'finite',
  )
})
it('decodes locally and revokes each owned object URL exactly once', async () => {
  const mock = mockLocalImages(),
    fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  const result = loadLocalImage(imageFile(), new AbortController().signal)
  await waitFor(() => expect(mock.images).toHaveLength(1))
  mock.images[0].onload!()
  const image = await result
  expect(image.width).toBe(1000)
  expect(image.url).toBe('blob:local-fixture-1')
  expect(fetch).not.toHaveBeenCalled()
  expect(mock.revoke).not.toHaveBeenCalled()
  image.dispose()
  image.dispose()
  expect(mock.revoke).toHaveBeenCalledExactlyOnceWith(image.url)
})
it.each([
  [8193, 32],
  [6000, 6000],
  [31, 400],
])(
  'rejects excessive or undersized dimensions %s × %s',
  async (width, height) => {
    const mock = mockLocalImages(),
      pending = loadLocalImage(imageFile(), new AbortController().signal)
    const check = expect(pending).rejects.toThrow('dimensions')
    await waitFor(() => expect(mock.images).toHaveLength(1))
    mock.images[0].naturalWidth = width
    mock.images[0].naturalHeight = height
    mock.images[0].onload!()
    await check
    expect(mock.revoke).toHaveBeenCalledTimes(1)
  },
)
it('releases failed and cancelled decodes and settles their promises', async () => {
  const mock = mockLocalImages(),
    controller = new AbortController()
  const first = loadLocalImage(imageFile(), controller.signal),
    check = expect(first).rejects.toMatchObject({ name: 'AbortError' })
  await waitFor(() => expect(mock.images).toHaveLength(1))
  controller.abort()
  await check
  expect(mock.images[0].onload).toBeNull()
  expect(mock.images[0].src).toBe('')
  const second = loadLocalImage(imageFile(), new AbortController().signal),
    failed = expect(second).rejects.toThrow('decoded')
  await waitFor(() => expect(mock.images).toHaveLength(2))
  mock.images[1].onerror!()
  await failed
  expect(mock.revoke).toHaveBeenCalledTimes(2)
})
it('rejects unsupported, oversized, spoofed and animated inputs before creating an image URL', async () => {
  const mock = mockLocalImages(),
    signal = new AbortController().signal
  await expect(
    loadLocalImage(imageFile(new Uint8Array([1]), 'image/svg+xml'), signal),
  ).rejects.toThrow('PNG')
  const huge = imageFile()
  Object.defineProperty(huge, 'size', { value: 10 * 1024 * 1024 + 1 })
  await expect(loadLocalImage(huge, signal)).rejects.toThrow('10 MiB')
  await expect(
    loadLocalImage(imageFile(new TextEncoder().encode('<svg/>')), signal),
  ).rejects.toThrow('contents')
  const apng = new Uint8Array(20)
  apng.set(pngHeader())
  apng.set(new TextEncoder().encode('acTL'), 12)
  await expect(loadLocalImage(imageFile(apng), signal)).rejects.toThrow(
    'Animated',
  )
  const webp = new Uint8Array(20)
  webp.set(new TextEncoder().encode('RIFF'), 0)
  webp.set(new TextEncoder().encode('WEBPANIM'), 8)
  await expect(
    loadLocalImage(imageFile(webp, 'image/webp'), signal),
  ).rejects.toThrow('Animated')
  expect(mock.create).not.toHaveBeenCalled()
})
