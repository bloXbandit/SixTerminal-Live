# Vendored third-party

**pdf.js 4.0.379** — Mozilla, Apache License 2.0.
`pdf.min.mjs` + `pdf.worker.min.mjs`, taken unmodified from the official dist.

Vendored rather than loaded from a CDN so PDF import keeps working offline and
behind corporate networks that block CDNs — the same "offline by default"
principle the deterministic importer is built on. The PDF itself is parsed in
the browser and never uploaded.
