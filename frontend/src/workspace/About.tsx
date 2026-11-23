function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 .8a11.2 11.2 0 0 0-3.54 21.83c.56.1.77-.24.77-.54v-2.1c-3.13.68-3.79-1.33-3.79-1.33-.51-1.3-1.25-1.65-1.25-1.65-1.03-.7.08-.69.08-.69 1.13.08 1.73 1.16 1.73 1.16 1 1.71 2.63 1.21 3.27.93.1-.72.39-1.21.71-1.49-2.5-.28-5.13-1.25-5.13-5.57 0-1.23.44-2.23 1.15-3.01-.12-.29-.5-1.43.11-2.97 0 0 .94-.3 3.08 1.15A10.7 10.7 0 0 1 12 6.14c.95 0 1.9.13 2.8.38 2.14-1.45 3.08-1.15 3.08-1.15.61 1.54.23 2.68.11 2.97.72.78 1.15 1.78 1.15 3.01 0 4.33-2.63 5.28-5.14 5.56.4.35.76 1.03.76 2.08v3.1c0 .3.2.65.78.54A11.2 11.2 0 0 0 12 .8Z"
      />
    </svg>
  )
}
function LinkedInIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path
        fill="currentColor"
        d="M20.5 2h-17A1.5 1.5 0 0 0 2 3.5v17A1.5 1.5 0 0 0 3.5 22h17a1.5 1.5 0 0 0 1.5-1.5v-17A1.5 1.5 0 0 0 20.5 2ZM8 19H5V9h3ZM6.5 7.7A1.7 1.7 0 1 1 6.5 4a1.7 1.7 0 0 1 0 3.7ZM19 19h-3v-5.1c0-1.3-.5-2.1-1.6-2.1-1.2 0-1.8.9-1.8 2.1V19H9.7V9h2.8v1.4A3.2 3.2 0 0 1 15.4 9c2.2 0 3.6 1.5 3.6 4.3Z"
      />
    </svg>
  )
}
export function About() {
  return (
    <article className="about-page" aria-labelledby="about-title">
      <h1 id="about-title">About ShapeFinder</h1>
      <p className="about-lead">
        Draw a pattern and compare it with recent stock price sequences.
      </p>
      <div className="about-grid">
        <section>
          <h2>How the scanner works</h2>
          <p>
            Start with a drawing, a description, an image trace, or a section of
            a stock chart. Scan for full matches or patterns whose opening
            portion is forming. Similarity measures alignment, while estimated
            completion measures how much of the shape has formed.
          </p>
          <p>
            Coverage reflects the stocks actually evaluated. Match scores
            describe observed price history and do not forecast future prices.
          </p>
        </section>
        <section>
          <h2>Created by Muneer Mahmoud</h2>
          <div className="profile-links">
            <a
              href="https://github.com/Muneer-CS/ShapeFinder"
              target="_blank"
              rel="noopener noreferrer"
            >
              <GitHubIcon />
              Project repository
            </a>
            <a
              href="https://github.com/Muneer-CS"
              target="_blank"
              rel="noopener noreferrer"
            >
              <GitHubIcon />
              Muneer Mahmoud on GitHub
            </a>
            <a
              href="https://ca.linkedin.com/in/muneer-mahmoud"
              target="_blank"
              rel="noopener noreferrer"
            >
              <LinkedInIcon />
              Muneer Mahmoud on LinkedIn
            </a>
          </div>
        </section>
      </div>
    </article>
  )
}
