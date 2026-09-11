import { useEffect, useState } from 'react'
import { getHealth, type HealthResponse } from './api/health'

type ConnectionState =
  | { kind: 'checking' }
  | { kind: 'connected'; health: HealthResponse }
  | { kind: 'offline' }

function App() {
  const [connection, setConnection] = useState<ConnectionState>({
    kind: 'checking',
  })

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal)
      .then((health) => setConnection({ kind: 'connected', health }))
      .catch((error: unknown) => {
        if (error instanceof Error && error.name !== 'AbortError') {
          setConnection({ kind: 'offline' })
        }
      })
    return () => controller.abort()
  }, [])

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="/" aria-label="ShapeFinder home">
          <span className="brand-mark" aria-hidden="true">
            ⌁
          </span>
          ShapeFinder
        </a>
        <div className={`status status-${connection.kind}`} role="status">
          <span className="status-dot" aria-hidden="true" />
          {connection.kind === 'checking' && 'Checking API'}
          {connection.kind === 'connected' && 'API connected'}
          {connection.kind === 'offline' && 'API unavailable'}
        </div>
      </header>

      <section className="workspace" aria-labelledby="page-title">
        <div className="intro">
          <p className="eyebrow">Pattern research workspace</p>
          <h1 id="page-title">
            Find the shape,
            <br />
            not the price.
          </h1>
          <p className="lede">
            Compare how markets moved across time—without confusing a stock’s
            price level with its behaviour.
          </p>
        </div>

        <div
          className="chart-card"
          aria-label="Future comparison workspace preview"
        >
          <div className="chart-toolbar">
            <div>
              <span className="label">Reference</span>
              <strong>Stock &amp; period</strong>
            </div>
            <span className="phase-badge">Foundation ready</span>
          </div>
          <div className="chart-area" aria-hidden="true">
            <svg viewBox="0 0 720 260" preserveAspectRatio="none">
              <defs>
                <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#6ee7b7" stopOpacity=".28" />
                  <stop offset="1" stopColor="#6ee7b7" stopOpacity="0" />
                </linearGradient>
              </defs>
              <path className="gridline" d="M0 52H720M0 130H720M0 208H720" />
              <path
                className="area"
                d="M0 217 C45 202 66 209 102 180 S155 196 199 139 S270 171 313 116 S378 132 420 81 S483 122 523 67 S596 92 632 44 S685 69 720 24 V260 H0Z"
              />
              <path
                className="line"
                d="M0 217 C45 202 66 209 102 180 S155 196 199 139 S270 171 313 116 S378 132 420 81 S483 122 523 67 S596 92 632 44 S685 69 720 24"
              />
            </svg>
            <span className="axis-label axis-start">START</span>
            <span className="axis-label axis-end">END</span>
          </div>
          <div className="future-controls">
            <button disabled>Choose reference</button>
            <p>
              Reference selection and similarity search arrive in a later phase.
            </p>
          </div>
        </div>
      </section>

      <footer>
        <span>Phase 1</span>
        <span>Historical pattern similarity · No predictions</span>
      </footer>
    </main>
  )
}

export default App
