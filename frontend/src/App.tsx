function App() {
  return (
    <main className="app-shell">
      <section className="intro" aria-labelledby="page-title">
        <p className="eyebrow">Reviewer-assist prototype</p>
        <h1 id="page-title">Alcohol Label Verification</h1>
        <p className="summary">
          Compare expected application values with visible label artwork while keeping uncertain
          and regulatory decisions with a human reviewer.
        </p>
        <div className="status" role="status">
          <span aria-hidden="true" className="status-dot" />
          P0 review workflow is being implemented
        </div>
      </section>
    </main>
  )
}

export default App
