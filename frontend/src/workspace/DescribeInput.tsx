export function DescribeInput({
  value,
  onChange,
  onGenerate,
}: {
  value: string
  onChange: (value: string) => void
  onGenerate: () => void
}) {
  return (
    <section className="panel" aria-label="Describe a pattern">
      <h2>Describe a shape</h2>
      <p className="helper">
        <code>up 3, down 1, up 2</code>. Durations set width; each up/down moves
        one shape unit. Parsed locally with a supported grammar.
      </p>
      <label>
        Pattern description
        <textarea
          value={value}
          maxLength={20000}
          rows={2}
          onChange={(e) => onChange(e.target.value)}
          placeholder="up 3, down 1, up 2"
        />
      </label>
      <details>
        <summary>Description syntax</summary>
        <p className="helper">
          Or use exact coordinates: <code>points: 0:0, 0.4:-1, 1:0.6</code>. Use
          commas between all segments or points. x must increase from 0 to 1.
          Segment durations are relative units, not price percentages or candle
          counts. Flat keeps the same height.
        </p>
      </details>
      <button type="button" onClick={onGenerate}>
        Generate preview
      </button>
      <details>
        <summary>Preview workflow</summary>
        <p className="helper">
          Generation creates an editable draft in the shared editor. Accept
          preview deliberately replaces the applied pattern. Neither action
          starts a scan.
        </p>
      </details>
    </section>
  )
}
