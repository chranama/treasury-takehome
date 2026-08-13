function DownloadLink({ href, children }: { href: string; children: string }) {
  return (
    <a className="demo-download" href={href} download>
      {children}
    </a>
  )
}

function SingleReviewExamples() {
  return (
    <section className="demo-panel" aria-labelledby="single-demo-title">
      <div className="demo-heading">
        <div>
          <p className="step-label">Guided demo</p>
          <h2 id="single-demo-title">Try a supplied label</h2>
        </div>
        <p>All sample files are synthetic and contain no applicant data.</p>
      </div>

      <div className="demo-expected" role="note" aria-label="Expected application values">
        <strong>Use these expected values for all three labels</strong>
        <dl>
          <div><dt>Brand</dt><dd>OLD TOM</dd></div>
          <div><dt>Class/type</dt><dd>Kentucky Straight Bourbon Whiskey</dd></div>
          <div><dt>ABV</dt><dd>45</dd></div>
          <div><dt>Net contents</dt><dd>750 mL</dd></div>
        </dl>
      </div>

      <div className="demo-card-grid">
        <article className="demo-card">
          <h3>Clear match</h3>
          <p>Expected result: <strong>All checks passed</strong>.</p>
          <DownloadLink href="/demo/p0/matching-label.png">Download matching label</DownloadLink>
        </article>
        <article className="demo-card">
          <h3>Material mismatch</h3>
          <p>Expected result: <strong>Needs review</strong> because the label says 700 mL.</p>
          <DownloadLink href="/demo/p0/material-net-mismatch.png">
            Download mismatch label
          </DownloadLink>
        </article>
        <article className="demo-card">
          <h3>Unreadable evidence</h3>
          <p>Expected result: <strong>Needs review</strong>, without invented values.</p>
          <DownloadLink href="/demo/p0/unreadable-label.png">
            Download unreadable label
          </DownloadLink>
        </article>
      </div>
    </section>
  )
}

function BatchReviewExamples() {
  return (
    <section className="demo-panel" aria-labelledby="batch-demo-title">
      <div className="demo-heading">
        <div>
          <p className="step-label">Guided demo</p>
          <h2 id="batch-demo-title">Try a supplied batch</h2>
        </div>
        <p>Download each file in one example, then select its spreadsheet and images separately.</p>
      </div>

      <div className="demo-card-grid batch-demo-grid">
        <article className="demo-card">
          <h3>Blank templates</h3>
          <p>Start your own package while keeping the six column names unchanged.</p>
          <div className="demo-links">
            <DownloadLink href="/demo/templates/label-review-batch.xlsx">
              Download blank XLSX
            </DownloadLink>
            <DownloadLink href="/demo/templates/label-review-batch.csv">
              Download blank CSV
            </DownloadLink>
          </div>
        </article>

        <article className="demo-card">
          <h3>Two ready cases</h3>
          <p>
            Expected preflight: <strong>2 ready, 0 corrections</strong>. Processing should yield one
            pass and one 700 mL mismatch.
          </p>
          <div className="demo-links">
            <DownloadLink href="/demo/p1/valid/applications.csv">
              Download valid spreadsheet
            </DownloadLink>
            <DownloadLink href="/demo/p1/valid/matching-label.png">
              Download valid matching label
            </DownloadLink>
            <DownloadLink href="/demo/p1/valid/material-net-mismatch.png">
              Download valid mismatch label
            </DownloadLink>
          </div>
        </article>

        <article className="demo-card">
          <h3>Mixed preflight</h3>
          <p>
            Expected preflight: <strong>1 ready, 1 correction</strong>, plus an unreferenced-image
            warning.
          </p>
          <div className="demo-links">
            <DownloadLink href="/demo/p1/mixed-errors/applications.csv">
              Download mixed spreadsheet
            </DownloadLink>
            <DownloadLink href="/demo/p1/mixed-errors/matching-label.png">
              Download mixed matching label
            </DownloadLink>
            <DownloadLink href="/demo/p1/mixed-errors/replacement-label.png">
              Download mixed replacement label
            </DownloadLink>
          </div>
          <p className="demo-fix">
            To make both rows ready, change DEMO-FIX ABV to 45 and replace its missing image with
            <strong> replacement-label.png</strong>.
          </p>
        </article>
      </div>
    </section>
  )
}

export function DemoExamples({ batch }: { batch: boolean }) {
  return batch ? <BatchReviewExamples /> : <SingleReviewExamples />
}
