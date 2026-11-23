import { fireEvent, screen } from '@testing-library/react'
// Numeric tools are intentionally disclosed on demand in the compact workspace.
export function pointField(name: string) {
  const summary = screen.getByText('Point coordinates and keyboard tools')
  if (!(summary.closest('details') as HTMLDetailsElement).open)
    fireEvent.click(summary)
  return screen.getByLabelText(name)
}
export function regionField(name: string) {
  const summary = screen.getByText('Image and chart region')
  if (!(summary.closest('details') as HTMLDetailsElement).open)
    fireEvent.click(summary)
  return screen.getByLabelText(name)
}
