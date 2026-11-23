// Same-origin, blocking bootstrap before styles or React mount.
;(function () {
  let preference = null
  try {
    const stored = localStorage.getItem('shapefinder.theme')
    if (stored === 'dark' || stored === 'light') preference = stored
  } catch {
    /* Device preference works without storage. */
  }
  const theme =
    preference ||
    (window.matchMedia &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light')
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
})()
