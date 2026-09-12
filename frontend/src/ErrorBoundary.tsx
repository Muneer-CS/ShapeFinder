import { Component, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { failed: boolean }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  render() {
    if (this.state.failed)
      return (
        <main className="fatal-error" role="alert">
          <p className="eyebrow">ShapeFinder</p>
          <h1>Something went wrong.</h1>
          <p>
            The application could not display this page. Reload to try again.
          </p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload application
          </button>
        </main>
      )
    return this.props.children
  }
}
