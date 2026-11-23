import { useEffect, useRef, useState } from 'react'
import {
  cropProblem,
  fullCrop,
  loadLocalImage,
  type Crop,
  type LocalImage,
  type TraceView,
} from '../pattern/localImage'

type Props = {
  view: TraceView | null
  onView: (view: TraceView | null, sourceChanged: boolean) => void
  onDirty: () => void
  onLoading: (loading: boolean) => void
  onPreview: () => void
}
const fields = ['left', 'top', 'right', 'bottom'] as const
const percentDraft = (crop: Crop) =>
  Object.fromEntries(
    fields.map((key) => [key, String(crop[key] * 100)]),
  ) as Record<keyof Crop, string>
export function ScreenshotInput({
  view,
  onView,
  onDirty,
  onLoading,
  onPreview,
}: Props) {
  const [bounds, setBounds] = useState(() => percentDraft(fullCrop()))
  const [message, setMessage] = useState(''),
    [loading, setLoading] = useState(false)
  const resource = useRef<LocalImage | null>(null)
  const pending = useRef<AbortController | null>(null)
  const regionGesture = useRef<{
    pointer: number
    x: number
    y: number
  } | null>(null)
  const [regionPreview, setRegionPreview] = useState<Crop | null>(null)
  const [regionOpen, setRegionOpen] = useState(true)
  useEffect(
    () => () => {
      const controller = pending.current
      pending.current = null
      controller?.abort()
      resource.current?.dispose()
      resource.current = null
    },
    [],
  )
  async function load(file?: File) {
    if (!file) return
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setLoading(true)
    onLoading(true)
    setMessage('')
    try {
      const image = await loadLocalImage(file, controller.signal)
      if (pending.current !== controller || controller.signal.aborted) {
        image.dispose()
        return
      }
      resource.current?.dispose()
      resource.current = image
      setRegionOpen(true)
      setBounds(percentDraft(fullCrop()))
      onView({ image, crop: fullCrop(), zoom: 1 }, true)
      setMessage(
        'Image loaded locally. Select the chart region, then create a tracing preview. No image content or file metadata is uploaded.',
      )
    } catch (error) {
      if (pending.current === controller && !controller.signal.aborted)
        setMessage((error as Error).message)
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setLoading(false)
        onLoading(false)
      }
    }
  }
  const crop = Object.fromEntries(
    fields.map((key) => [
      key,
      bounds[key].trim() ? Number(bounds[key]) / 100 : NaN,
    ]),
  ) as Crop
  const problem = cropProblem(crop)
  const dirty = !!view && fields.some((key) => crop[key] !== view.crop[key])
  const shown = regionPreview ?? (problem ? (view?.crop ?? fullCrop()) : crop)
  return (
    <section className="panel" aria-label="Local screenshot tracing">
      <h2>Trace an image</h2>
      <details>
        <summary>Image tracing guide</summary>
        <p className="helper">
          PNG, JPEG or WebP, up to 10 MiB and 24 million pixels; 32 to 8192
          pixels per side. This is manual tracing, with no automatic price,
          timestamp or named-pattern interpretation. Images and filenames stay
          in this browser.
        </p>
      </details>
      <label>
        Choose chart image
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          onChange={(e) => {
            const file = e.target.files?.[0]
            e.target.value = ''
            void load(file)
          }}
        />
      </label>
      {loading && (
        <button
          type="button"
          onClick={() => {
            pending.current?.abort()
            pending.current = null
            setLoading(false)
            onLoading(false)
            setMessage(
              'Image import cancelled. The previous image and applied pattern were kept.',
            )
          }}
        >
          Cancel image import
        </button>
      )}
      {view && (
        <>
          <details
            open={regionOpen}
            onToggle={(e) => setRegionOpen(e.currentTarget.open)}
          >
            <summary>Image and chart region</summary>
            <p className="helper">
              Local image: {view.image.width} × {view.image.height}. Drag a
              rectangle on this overview, or enter percentages. Apply chart
              region deliberately changes tracing bounds and requires a fresh
              interpretation.
            </p>
            <svg
              className="crop-overview"
              role="group"
              aria-label="Chart region selection"
              viewBox="0 0 1000 400"
              preserveAspectRatio="none"
              style={{
                aspectRatio: `${view.image.width} / ${view.image.height}`,
              }}
              onPointerDown={(e) => {
                if (e.button !== 0 || regionGesture.current) return
                const r = e.currentTarget.getBoundingClientRect()
                regionGesture.current = {
                  pointer: e.pointerId,
                  x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                  y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
                }
                e.currentTarget.setPointerCapture?.(e.pointerId)
                e.preventDefault()
              }}
              onPointerMove={(e) => {
                const g = regionGesture.current
                if (!g || g.pointer !== e.pointerId) return
                const r = e.currentTarget.getBoundingClientRect(),
                  x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                  y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))
                setRegionPreview({
                  left: Math.min(x, g.x),
                  right: Math.max(x, g.x),
                  top: Math.min(y, g.y),
                  bottom: Math.max(y, g.y),
                })
              }}
              onPointerUp={(e) => {
                const g = regionGesture.current
                if (!g || g.pointer !== e.pointerId) return
                const r = e.currentTarget.getBoundingClientRect(),
                  x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
                  y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))
                const next = {
                  left: Math.min(x, g.x),
                  right: Math.max(x, g.x),
                  top: Math.min(y, g.y),
                  bottom: Math.max(y, g.y),
                }
                regionGesture.current = null
                setRegionPreview(null)
                if (e.currentTarget.hasPointerCapture?.(e.pointerId))
                  e.currentTarget.releasePointerCapture(e.pointerId)
                if (cropProblem(next)) {
                  setMessage(cropProblem(next)!)
                  return
                }
                setBounds(percentDraft(next))
                onDirty()
              }}
              onPointerCancel={() => {
                regionGesture.current = null
                setRegionPreview(null)
              }}
              onLostPointerCapture={() => {
                regionGesture.current = null
                setRegionPreview(null)
              }}
            >
              <image
                href={view.image.url}
                width="1000"
                height="400"
                preserveAspectRatio="none"
              />
              <rect
                className="crop-outline"
                x={shown.left * 1000}
                y={shown.top * 400}
                width={(shown.right - shown.left) * 1000}
                height={(shown.bottom - shown.top) * 400}
              />
            </svg>
            <fieldset>
              <legend>Chart region (%)</legend>
              <div className="two-fields">
                {fields.map((key) => (
                  <label key={key}>
                    {key[0].toUpperCase() + key.slice(1)} bound (%)
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="any"
                      value={bounds[key]}
                      onChange={(e) => {
                        setBounds({ ...bounds, [key]: e.target.value })
                        onDirty()
                      }}
                    />
                  </label>
                ))}
              </div>
            </fieldset>
            {problem && <p role="alert">{problem}</p>}
            <div className="toolbar">
              <button
                type="button"
                disabled={!!problem || !dirty || loading}
                onClick={() => {
                  if (!problem) {
                    onView({ ...view, crop }, true)
                    setMessage(
                      'Chart region changed. Create a new tracing preview; the applied geometry has not moved.',
                    )
                  }
                }}
              >
                Apply chart region
              </button>
              <button
                type="button"
                disabled={!!problem || dirty || loading}
                onClick={() => {
                  onPreview()
                  setRegionOpen(false)
                }}
              >
                Create tracing preview
              </button>
            </div>
          </details>
          <label>
            Trace zoom
            <input
              type="range"
              min="1"
              max="3"
              step="0.25"
              value={view.zoom}
              onChange={(e) =>
                onView({ ...view, zoom: Number(e.target.value) }, false)
              }
            />
          </label>
          <details>
            <summary>Tracing coordinates</summary>
            <p className="helper">
              Zoom changes the view only. The selected left/right bounds map to
              x=0/1; the top maps to y=1 and bottom to y=-1. Trace across both
              boundaries, then edit the points and accept the preview.
            </p>
          </details>
        </>
      )}
      <p role="status">{message}</p>
    </section>
  )
}
