/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { usePatternInput } from './usePatternInput'
afterEach(() => vi.unstubAllGlobals())
it('ignores delayed acceptance hashes after source changes, including a newer acceptance', async () => {
  const pending: { data: BufferSource; done: (value: ArrayBuffer) => void }[] =
    []
  vi.stubGlobal('crypto', {
    subtle: {
      digest: vi.fn(
        (_algorithm, data: BufferSource) =>
          new Promise<ArrayBuffer>((done) => pending.push({ data, done })),
      ),
    },
  })
  const { result } = renderHook(() => usePatternInput(vi.fn()))
  act(() => {
    result.current.choose('text')
    result.current.descriptionChanged('up 3, down 1, up 2')
  })
  act(() => result.current.generate())
  let first!: Promise<void>
  act(() => {
    first = result.current.accept()
  })
  act(() => result.current.descriptionChanged('down 1, up 2'))
  act(() => result.current.generate())
  let second!: Promise<void>
  act(() => {
    second = result.current.accept()
  })
  await act(async () => {
    pending[1].done(await webcrypto.subtle.digest('SHA-256', pending[1].data))
    await second
  })
  const accepted = result.current.ack,
    points = result.current.applied.points
  await act(async () => {
    pending[0].done(await webcrypto.subtle.digest('SHA-256', pending[0].data))
    await first
  })
  expect(result.current.ack).toEqual(accepted)
  expect(result.current.applied.points).toEqual(points)
  expect(points).toEqual([
    { x: 0, y: 0 },
    { x: 1 / 3, y: -1 },
    { x: 1, y: 0 },
  ])
})
