import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { imageFrame, type TraceView } from '../pattern/localImage'
import type { PointerEvent as ReactPointerEvent } from 'react'
import {
  finishStroke,
  screenPoint,
  yDomain,
  type Point,
} from '../pattern/model'

type Props = {
  points: Point[]
  onEdit: (points: Point[]) => void
  onDraw: (points: Point[]) => void
  onClear: () => void
  onReset: () => void
  onUndo: () => void
  onRedo: () => void
  canUndo: boolean
  canRedo: boolean
  canReset: boolean
  trace?: TraceView | null
  drawingEnabled?: boolean
  sourceLabel?: string
}
type Gesture = {
  pointer: number
  mode: 'draw' | 'edit'
  index: number
  raw: Point[]
  original: Point[]
  domain: [number, number]
  origin: { x: number; y: number }
  moved: boolean
}
export function PatternEditor({
  points,
  onEdit,
  onDraw,
  onClear,
  onReset,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  canReset,
  trace = null,
  drawingEnabled = true,
  sourceLabel,
}: Props) {
  const clip = useId()
  const viewport = useRef<HTMLDivElement>(null)
  const [drawing, setDrawing] = useState(false)
  const [selected, setSelected] = useState(0)
  const [preview, setPreview] = useState<Point[] | null>(null)
  const [previewDomain, setPreviewDomain] = useState<[number, number]>([-1, 1])
  const [message, setMessage] = useState('')
  const gesture = useRef<Gesture | null>(null)
  const svg = useRef<SVGSVGElement>(null)
  const cancelGesture = useCallback((reason: string) => {
    const current = gesture.current
    if (!current) return
    gesture.current = null
    setPreview(null)
    setMessage(reason)
    if (svg.current?.hasPointerCapture?.(current.pointer))
      svg.current.releasePointerCapture(current.pointer)
  }, [])
  useEffect(() => {
    const resized = () =>
      cancelGesture(
        'View resized. The unfinished gesture was discarded; the pattern was kept.',
      )
    window.addEventListener('resize', resized)
    return () => {
      window.removeEventListener('resize', resized)
      gesture.current = null
    }
  }, [cancelGesture])
  const shown = preview ?? points
  const domain: [number, number] = trace
    ? [-1, 1]
    : preview
      ? previewDomain
      : yDomain(points)
  const index = Math.min(selected, Math.max(0, points.length - 1))
  const point = points[index]
  const [numeric, setNumeric] = useState<{
    index: number
    x: string
    y: string
    basis: Point[]
  } | null>(null)
  const x =
    numeric?.index === index && numeric.basis === points
      ? numeric.x
      : String(point?.x ?? '')
  const y =
    numeric?.index === index && numeric.basis === points
      ? numeric.y
      : String(point?.y ?? '')
  const plotRect = (element: SVGSVGElement) => {
    const rect = element.getBoundingClientRect()
    return {
      left: rect.left + (rect.width * 24) / 1048,
      top: rect.top + (rect.height * 24) / 448,
      width: (rect.width * 1000) / 1048,
      height: (rect.height * 400) / 448,
    }
  }
  const project = (p: Point) =>
    `${p.x * 1000},${(400 * (domain[1] - p.y)) / (domain[1] - domain[0])}`
  function adjust(original: Point[], at: number, next: Point): Point[] {
    const result = original.map((p) => ({ ...p }))
    let x = at === 0 ? 0 : at === original.length - 1 ? 1 : next.x
    if (at > 0 && at < original.length - 1) {
      const lower = original[at - 1].x,
        upper = original[at + 1].x
      if (x <= lower) x = (lower + original[at].x) / 2
      if (x >= upper) x = (original[at].x + upper) / 2
      if (!(x > lower && x < upper)) x = original[at].x
    }
    result[at] = {
      x,
      y: Math.max(trace ? -1 : -1000, Math.min(trace ? 1 : 1000, next.y)),
    }
    return result
  }
  function begin(event: ReactPointerEvent<SVGSVGElement>) {
    if (event.button !== 0 || gesture.current) return
    const targetIndex = (event.target as Element).getAttribute('data-point')
    if (!drawing && targetIndex === null) return
    const at = Number(targetIndex ?? 0)
    const p = screenPoint(
      event.clientX,
      event.clientY,
      plotRect(event.currentTarget),
      drawing ? [-1, 1] : domain,
    )
    if (drawing && trace && p.x <= 4 / plotRect(event.currentTarget).width)
      p.x = 0
    gesture.current = {
      pointer: event.pointerId,
      mode: drawing ? 'draw' : 'edit',
      index: at,
      raw: [p],
      original: points,
      domain: drawing ? [-1, 1] : domain,
      origin: { x: event.clientX, y: event.clientY },
      moved: false,
    }
    setPreviewDomain(drawing ? [-1, 1] : domain)
    setSelected(at)
    setNumeric(null)
    setMessage('')
    event.currentTarget.setPointerCapture?.(event.pointerId)
    setPreview(drawing ? [p] : points)
    event.preventDefault()
  }
  function move(event: ReactPointerEvent<SVGSVGElement>) {
    const g = gesture.current
    if (!g || event.pointerId !== g.pointer) return
    const p = screenPoint(
      event.clientX,
      event.clientY,
      plotRect(event.currentTarget),
      g.domain,
    )
    if (g.mode === 'draw') {
      g.raw.push(p)
      setPreview([...g.raw])
    } else {
      g.raw = [p]
      if (
        Math.hypot(event.clientX - g.origin.x, event.clientY - g.origin.y) >= 3
      )
        g.moved = true
      if (g.moved) setPreview(adjust(g.original, g.index, p))
    }
  }
  function end(event: ReactPointerEvent<SVGSVGElement>, cancel = false) {
    const g = gesture.current
    if (!g || event.pointerId !== g.pointer) return
    if (!cancel) move(event)
    gesture.current = null
    setPreview(null)
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId)
    if (cancel) {
      setMessage('Gesture cancelled. The pattern was kept.')
      return
    }
    if (g.mode === 'draw') {
      try {
        if (trace) {
          if (g.raw.at(-1)!.x >= 1 - 4 / plotRect(event.currentTarget).width)
            g.raw.at(-1)!.x = 1
          if (g.raw[0].x !== 0 || g.raw.at(-1)!.x !== 1)
            throw new Error(
              'Trace discarded: begin on the left chart boundary and finish on the right boundary. Endpoints snap within 4 screen pixels.',
            )
        }
        const next = finishStroke(g.raw)
        onDraw(next)
        setSelected(0)
        setDrawing(false)
        setMessage(
          `${g.raw.length} samples became ${next.length} editable points. Duplicate x positions use the last height. Review the visible shape.`,
        )
      } catch (error) {
        setMessage((error as Error).message)
      }
    } else if (g.moved) onEdit(adjust(g.original, g.index, g.raw[0]))
  }
  function applyNumeric() {
    if (!point) return
    const next = { x: Number(x), y: Number(y) }
    if (
      !x.trim() ||
      !y.trim() ||
      !Number.isFinite(next.x) ||
      !Number.isFinite(next.y) ||
      Math.abs(next.y) > (trace ? 1 : 1000) ||
      (index > 0 && next.x <= points[index - 1].x) ||
      (index < points.length - 1 && next.x >= points[index + 1].x) ||
      (index === 0 && next.x !== 0) ||
      (index === points.length - 1 && next.x !== 1)
    ) {
      setMessage(
        `Keep y between ${trace ? '-1 and 1 within the chart region' : '-1000 and 1000'}, and x strictly between its neighbours. Endpoint x is fixed.`,
      )
      return
    }
    onEdit(points.map((p, i) => (i === index ? next : p)))
    setNumeric(null)
    setMessage('Coordinates updated.')
  }
  return (
    <section className="panel editor-panel" aria-labelledby="editor-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">{sourceLabel ?? 'Pattern'}</span>
          <h2 id="editor-title">Shape editor</h2>
        </div>
        <span>{points.length} / 256 points</span>
      </div>
      <div className="toolbar editor-toolbar">
        <button
          className="primary"
          type="button"
          aria-pressed={drawing}
          disabled={!drawingEnabled || (!!trace && trace.zoom !== 1)}
          onClick={() => {
            setDrawing(!drawing)
            setMessage(
              'Draw left to right. Backwards strokes are discarded. Vertical duplicates keep the latest height.',
            )
          }}
        >
          {drawing
            ? 'Cancel drawing'
            : trace
              ? 'Trace pattern'
              : 'Draw pattern'}
        </button>
        <button type="button" onClick={onUndo} disabled={!canUndo}>
          Undo
        </button>
        <button type="button" onClick={onRedo} disabled={!canRedo}>
          Redo
        </button>
        <button type="button" onClick={onClear} disabled={!points.length}>
          Clear
        </button>
        <button type="button" onClick={onReset} disabled={!canReset}>
          Reset pattern
        </button>
      </div>
      {trace && (
        <p className="helper">
          Trace manually from the left chart boundary to the right. Endpoints
          snap within 4 screen pixels. Image height maps upward from -1 to 1.
          Set Trace zoom to 1 for a full-width stroke. Zoom and pan change only
          the view and remain available for point editing.
        </p>
      )}
      {trace && trace.zoom > 1 && (
        <div className="toolbar">
          <button
            type="button"
            onClick={() => {
              cancelGesture(
                'View moved. The unfinished gesture was discarded; the pattern was kept.',
              )
              viewport.current?.scrollBy({
                left: -(viewport.current.clientWidth * 0.6),
              })
            }}
          >
            Pan left
          </button>
          <button
            type="button"
            onClick={() => {
              cancelGesture(
                'View moved. The unfinished gesture was discarded; the pattern was kept.',
              )
              viewport.current?.scrollBy({
                left: viewport.current.clientWidth * 0.6,
              })
            }}
          >
            Pan right
          </button>
        </div>
      )}
      <div ref={viewport} className={trace ? 'trace-viewport' : undefined}>
        <div
          className="canvas-frame"
          style={trace ? { width: `${trace.zoom * 100}%` } : undefined}
        >
          <svg
            ref={svg}
            className={`pattern-canvas ${drawing ? 'drawing' : ''}`}
            viewBox="-24 -24 1048 448"
            preserveAspectRatio="none"
            style={
              trace
                ? {
                    height: 'auto',
                    aspectRatio: String(
                      (((trace.image.width *
                        (trace.crop.right - trace.crop.left)) /
                        (trace.image.height *
                          (trace.crop.bottom - trace.crop.top))) *
                        (1048 / 1000)) /
                        (448 / 400),
                    ),
                  }
                : undefined
            }
            role="group"
            aria-label="Editable pattern drawing area"
            tabIndex={-1}
            onPointerDown={begin}
            onPointerMove={move}
            onPointerUp={(event) => end(event)}
            onPointerCancel={(event) => end(event, true)}
            onKeyDown={(event) => {
              if (!(event.ctrlKey || event.metaKey) || event.altKey) return
              const key = event.key.toLowerCase()
              if (key === 'z' || key === 'y') {
                event.preventDefault()
                if (key === 'y' || event.shiftKey) onRedo()
                else onUndo()
              }
            }}
            onLostPointerCapture={(event) => {
              if (gesture.current?.pointer === event.pointerId) end(event, true)
            }}
          >
            <rect
              x="0"
              y="0"
              width="1000"
              height="400"
              className="plot-background"
            />
            {trace && (
              <>
                <defs>
                  <clipPath id={clip}>
                    <rect x="0" y="0" width="1000" height="400" />
                  </clipPath>
                </defs>
                <image
                  data-testid="trace-image"
                  href={trace.image.url}
                  {...imageFrame(trace.crop)}
                  preserveAspectRatio="none"
                  clipPath={`url(#${clip})`}
                />
              </>
            )}
            {!trace &&
              [100, 200, 300].map((y) => (
                <line
                  key={y}
                  x1="0"
                  x2="1000"
                  y1={y}
                  y2={y}
                  className="grid-line"
                />
              ))}
            {!trace &&
              [200, 400, 600, 800].map((x) => (
                <line
                  key={x}
                  x1={x}
                  x2={x}
                  y1="0"
                  y2="400"
                  className="grid-line"
                />
              ))}
            <polyline
              points={shown.map(project).join(' ')}
              className="pattern-line"
            />
            {!drawing &&
              shown.map((p, i) => (
                <g key={i}>
                  <circle
                    aria-hidden="true"
                    data-point={i}
                    cx={p.x * 1000}
                    cy={(400 * (domain[1] - p.y)) / (domain[1] - domain[0])}
                    r="9"
                    className="point-hit"
                  />
                  <circle
                    data-point={i}
                    cx={p.x * 1000}
                    cy={(400 * (domain[1] - p.y)) / (domain[1] - domain[0])}
                    r="9"
                    tabIndex={0}
                    role="button"
                    aria-label={`Point ${i + 1}, x ${p.x.toFixed(3)}, y ${p.y.toPrecision(4)}`}
                    aria-pressed={index === i}
                    className={
                      index === i ? 'control-point selected' : 'control-point'
                    }
                    onFocus={() => {
                      setSelected(i)
                      setNumeric(null)
                    }}
                    onClick={() => {
                      setSelected(i)
                      setNumeric(null)
                    }}
                    onKeyDown={(event) => {
                      if (
                        event.key === 'Delete' &&
                        i > 0 &&
                        i < points.length - 1
                      ) {
                        onEdit(points.filter((_, at) => at !== i))
                        setSelected(i - 1)
                        event.preventDefault()
                      }
                      if (
                        ![
                          'ArrowUp',
                          'ArrowDown',
                          'ArrowLeft',
                          'ArrowRight',
                        ].includes(event.key)
                      )
                        return
                      event.preventDefault()
                      const step = event.shiftKey ? 0.1 : 0.01
                      onEdit(
                        adjust(points, i, {
                          x:
                            p.x +
                            (event.key === 'ArrowLeft'
                              ? -step
                              : event.key === 'ArrowRight'
                                ? step
                                : 0),
                          y:
                            p.y +
                            (event.key === 'ArrowUp'
                              ? step
                              : event.key === 'ArrowDown'
                                ? -step
                                : 0),
                        }),
                      )
                      setNumeric(null)
                    }}
                  />
                </g>
              ))}
          </svg>
          {!shown.length && !trace && (
            <p className="canvas-instruction">
              {drawing
                ? 'Draw from left to right'
                : 'Choose Draw pattern to sketch here'}
            </p>
          )}
        </div>
      </div>
      {!trace && (
        <div className="canvas-axis" aria-hidden="true">
          <span>Pattern start</span>
          <span>Pattern end</span>
        </div>
      )}
      <details className="point-tools">
        <summary>Point coordinates and keyboard tools</summary>
        <p className="helper">
          Arrow keys adjust a focused point by 0.01; Shift uses 0.1. Delete
          removes an interior point. Resizing keeps coordinates unchanged.
        </p>
        {!points.length && drawingEnabled && !trace && (
          <button
            type="button"
            onClick={() => {
              onDraw([
                { x: 0, y: 0 },
                { x: 1, y: 0 },
              ])
              setDrawing(false)
              setSelected(0)
              setNumeric(null)
              svg.current?.focus()
              setMessage(
                'Two endpoints added. Press Tab to focus a point, then use the arrow keys.',
              )
            }}
          >
            Start with two points
          </button>
        )}
        <div className="coordinate-controls">
          <label>
            Selected point
            <select
              value={index}
              disabled={!point}
              onChange={(e) => {
                setSelected(Number(e.target.value))
                setNumeric(null)
              }}
            >
              {points.map((_, i) => (
                <option key={i} value={i}>
                  Point {i + 1}
                  {i === 0 || i === points.length - 1 ? ' (endpoint)' : ''}
                </option>
              ))}
            </select>
          </label>
          <label>
            X position
            <input
              type="number"
              step="any"
              value={x}
              disabled={!point || index === 0 || index === points.length - 1}
              onChange={(e) =>
                setNumeric({ index, x: e.target.value, y, basis: points })
              }
            />
          </label>
          <label>
            Y shape units
            <input
              type="number"
              step="any"
              value={y}
              disabled={!point}
              onChange={(e) =>
                setNumeric({ index, x, y: e.target.value, basis: points })
              }
            />
          </label>
          <button type="button" disabled={!point} onClick={applyNumeric}>
            Apply coordinates
          </button>
        </div>
        <div className="toolbar">
          <button
            type="button"
            disabled={points.length < 2 || points.length >= 256}
            onClick={() => {
              const at = Math.min(index, points.length - 2),
                a = points[at],
                b = points[at + 1],
                midpoint = (a.x + b.x) / 2
              if (midpoint === a.x || midpoint === b.x) {
                setMessage(
                  'There is no representable space between these points.',
                )
                return
              }
              onEdit([
                ...points.slice(0, at + 1),
                { x: midpoint, y: (a.y + b.y) / 2 },
                ...points.slice(at + 1),
              ])
              setSelected(at + 1)
              setNumeric(null)
            }}
          >
            Add midpoint
          </button>
          <button
            type="button"
            disabled={!point || index === 0 || index === points.length - 1}
            onClick={() => {
              onEdit(points.filter((_, i) => i !== index))
              setSelected(index - 1)
              setNumeric(null)
            }}
          >
            Remove point
          </button>
        </div>
      </details>
      <p className="editor-message" role="status">
        {message}
      </p>
    </section>
  )
}
