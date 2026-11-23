import { useEffect, useState } from 'react'
import { PatternWorkspace } from './workspace/PatternWorkspace'
import { About } from './workspace/About'
import { ThemeControl } from './workspace/ThemeControl'
export default function App() {
  const [page, setPage] = useState(() =>
    window.location.hash === '#about' ? 'about' : 'scanner',
  )
  useEffect(() => {
    const navigate = () =>
      setPage((current) =>
        window.location.hash === '#about'
          ? 'about'
          : ['', '#scanner'].includes(window.location.hash)
            ? 'scanner'
            : current,
      )
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="app-header">
        <a className="brand" href="#scanner" aria-label="ShapeFinder scanner">
          ShapeFinder<span>.</span>
        </a>
        <nav aria-label="Primary navigation">
          <a
            href="#scanner"
            aria-current={page === 'scanner' ? 'page' : undefined}
          >
            Scanner
          </a>
          <a href="#about" aria-current={page === 'about' ? 'page' : undefined}>
            About
          </a>
        </nav>
        <ThemeControl />
      </header>
      <main id="main-content" tabIndex={-1}>
        <div id="scanner" hidden={page !== 'scanner'}>
          <PatternWorkspace />
        </div>
        <div id="about" hidden={page !== 'about'}>
          <About />
        </div>
      </main>
    </div>
  )
}
