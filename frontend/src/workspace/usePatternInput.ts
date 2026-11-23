import { useEffect, useRef, useState } from 'react'
import {
  drawingProvenance,
  emptyPattern,
  patternHash,
  patternProblem,
  type Pattern,
  type Point,
} from '../pattern/model'
import { parseDescription } from '../pattern/textPattern'
import type { TraceView } from '../pattern/localImage'
import type { ScanRequest } from '../api/patternScans'

export type InputMethod = 'draw' | 'text' | 'screenshot_trace' | 'stock_chart'
type Interpreted = 'text' | 'screenshot_trace'
type Binding = { kind: Interpreted; version: number }
type Entry = { pattern: Pattern; source: Binding | null }
type History<T> = { past: T[]; present: T; future: T[]; baseline: T }
type Preview = { history: History<Pattern>; source: Binding; id: number }
const initial = <T>(value: T): History<T> => ({
  past: [],
  present: value,
  future: [],
  baseline: value,
})
const append = <T>(
  history: History<T>,
  value: T,
  baseline = false,
): History<T> => ({
  past: [...history.past.slice(-99), history.present],
  present: value,
  future: [],
  baseline: baseline ? value : history.baseline,
})
const travel = <T>(h: History<T>, redo: boolean): History<T> =>
  redo
    ? h.future.length
      ? {
          ...h,
          past: [...h.past, h.present],
          present: h.future[0],
          future: h.future.slice(1),
        }
      : h
    : h.past.length
      ? {
          ...h,
          past: h.past.slice(0, -1),
          present: h.past.at(-1)!,
          future: [h.present, ...h.future],
        }
      : h

export function usePatternInput(
  invalidateScan: (markPrevious?: boolean) => void,
) {
  const [method, setMethod] = useState<InputMethod>('draw')
  const [applied, setApplied] = useState(() =>
    initial<Entry>({ pattern: emptyPattern(), source: null }),
  )
  const [previews, setPreviews] = useState<Record<Interpreted, Preview | null>>(
    { text: null, screenshot_trace: null },
  )
  const [versions, setVersions] = useState({ text: 0, screenshot_trace: 0 })
  const versionRef = useRef(versions)
  const [revision, setRevision] = useState(1),
    revisionRef = useRef(1)
  const [ack, setAck] = useState<ScanRequest['preview_acknowledgement']>(null)
  const [accepting, setAccepting] = useState(false)
  const epoch = useRef(0)
  const previewSerial = useRef(0)
  const [description, setDescription] = useState('')
  const [sourceError, setSourceError] = useState('')
  const [trace, setTrace] = useState<TraceView | null>(null)
  const [imageLoading, setImageLoading] = useState(false)
  useEffect(
    () => () => {
      epoch.current++
    },
    [],
  )
  function cancelAcceptance() {
    epoch.current++
    setAccepting(false)
  }
  function cancelWork() {
    invalidateScan()
    cancelAcceptance()
    setSourceError('')
  }
  function bump() {
    revisionRef.current++
    setRevision(revisionRef.current)
    setAck(null)
    return revisionRef.current
  }
  function sourceChanged(kind: Interpreted) {
    cancelWork()
    const next = { ...versionRef.current, [kind]: versionRef.current[kind] + 1 }
    versionRef.current = next
    setVersions(next)
    if (applied.present.pattern.provenance.kind === kind) setAck(null)
  }
  function choose(next: InputMethod) {
    invalidateScan(false)
    cancelAcceptance()
    setSourceError('')
    setMethod(next)
  }
  const preview =
    method === 'text' || method === 'screenshot_trace' ? previews[method] : null
  const visible = preview?.history.present ?? applied.present.pattern
  const binding = preview?.source ?? applied.present.source
  const outdated = !!binding && binding.version !== versions[binding.kind]
  const interpreted =
    visible.provenance.kind === 'text' ||
    visible.provenance.kind === 'screenshot_trace'
  const needsAcceptance =
    !!preview || (interpreted && (!ack || ack.pattern_revision !== revision))
  const history = preview?.history
  function editApplied(
    pattern: Pattern,
    baseline = false,
    source = applied.present.source,
  ) {
    cancelWork()
    bump()
    setApplied((h) => append(h, { pattern, source }, baseline))
  }
  function editVisible(points: Point[], baseline = false) {
    if (preview) {
      cancelWork()
      const kind = preview.source.kind
      setPreviews((all) => ({
        ...all,
        [kind]: {
          ...preview,
          history: append(preview.history, { ...visible, points }, baseline),
        },
      }))
    } else editApplied({ ...visible, points }, baseline)
  }
  function newPreview(pattern: Pattern, kind: Interpreted) {
    cancelWork()
    const id = ++previewSerial.current
    setPreviews((all) => ({
      ...all,
      [kind]: {
        history: initial(pattern),
        source: { kind, version: versionRef.current[kind] },
        id,
      },
    }))
  }
  function generate() {
    try {
      newPreview(parseDescription(description), 'text')
    } catch (error) {
      cancelWork()
      setSourceError((error as Error).message)
    }
  }
  function undo(redo = false) {
    cancelWork()
    if (preview)
      setPreviews((all) => ({
        ...all,
        [preview.source.kind]: {
          ...preview,
          history: travel(preview.history, redo),
        },
      }))
    else {
      bump()
      setApplied((h) => travel(h, redo))
    }
  }
  async function accept() {
    if (
      accepting ||
      outdated ||
      imageLoading ||
      patternProblem(visible.points) ||
      !interpreted
    )
      return
    cancelWork()
    const token = ++epoch.current
    setAccepting(true)
    try {
      const hash = await patternHash(visible)
      if (epoch.current !== token) return
      let acceptedRevision = revisionRef.current
      if (preview) {
        acceptedRevision = bump()
        const entry = { pattern: visible, source: preview.source }
        setApplied((h) => append(h, entry, true))
        setPreviews((all) => ({ ...all, [preview.source.kind]: null }))
      }
      setAck({ pattern_revision: acceptedRevision, content_hash: hash })
    } catch (error) {
      if (epoch.current === token)
        setSourceError(
          `Preview could not be accepted: ${(error as Error).message}`,
        )
    } finally {
      if (epoch.current === token) setAccepting(false)
    }
  }
  const editorTrace =
    !outdated && visible.provenance.kind === 'screenshot_trace' ? trace : null
  return {
    method,
    choose,
    description,
    sourceError,
    trace,
    preview: !!preview,
    visible,
    applied: applied.present.pattern,
    revision,
    ack,
    outdated,
    needsAcceptance,
    accepting,
    blocked:
      !!preview ||
      (interpreted && (needsAcceptance || outdated)) ||
      imageLoading,
    canAccept:
      interpreted &&
      !outdated &&
      !imageLoading &&
      !patternProblem(visible.points),
    editorTrace,
    editorKey: `${method}:${preview?.id ?? 'applied'}:${binding ? versions[binding.kind] : 0}:${editorTrace?.zoom ?? 1}`,
    descriptionChanged: (text: string) => {
      setDescription(text)
      sourceChanged('text')
    },
    generate,
    accept,
    cancelPreview: () => {
      cancelWork()
      if (preview)
        setPreviews((all) => ({ ...all, [preview.source.kind]: null }))
    },
    imageLoading: (loading: boolean) => {
      if (loading) cancelWork()
      setImageLoading(loading)
    },
    sourceChanged: () => sourceChanged('screenshot_trace'),
    traceChanged: (view: TraceView | null, changed: boolean) => {
      if (changed) sourceChanged('screenshot_trace')
      setTrace(view)
    },
    createTrace: () => {
      if (trace)
        newPreview(
          {
            schema_version: 1,
            points: [],
            provenance: { ...drawingProvenance(), kind: 'screenshot_trace' },
          },
          'screenshot_trace',
        )
    },
    importStock: (pattern: Pattern) => {
      editApplied(pattern, true, null)
      setMethod('stock_chart')
    },
    editor: {
      points: visible.points,
      onEdit: (points: Point[]) => editVisible(points),
      onDraw: (points: Point[]) =>
        preview || editorTrace
          ? editVisible(points, true)
          : editApplied(
              { schema_version: 1, points, provenance: drawingProvenance() },
              true,
              null,
            ),
      onClear: () => editVisible([]),
      onReset: () =>
        preview
          ? editVisible(preview.history.baseline.points)
          : editApplied(
              applied.baseline.pattern,
              false,
              applied.baseline.source,
            ),
      canReset: history
        ? history.baseline.points.length > 0 &&
          history.present.points !== history.baseline.points
        : applied.baseline.pattern.points.length > 0 &&
          applied.present.pattern !== applied.baseline.pattern,
      onUndo: () => undo(),
      onRedo: () => undo(true),
      canUndo: !!(history?.past.length ?? applied.past.length),
      canRedo: !!(history?.future.length ?? applied.future.length),
    },
  }
}
