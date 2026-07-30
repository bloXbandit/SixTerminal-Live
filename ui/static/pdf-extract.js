/**
 * pdf-extract.js — Read a PDF's text in the BROWSER, never uploading it.
 *
 * Why: parsing a long PDF server-side means holding the file and a parser in
 * memory for the length of the request, which is what kills big imports on a
 * small host. Doing it here costs the server nothing, has no page limit, and
 * the file never leaves the machine at all.
 *
 * The output is deliberately plain text with the column spacing REBUILT from
 * the glyph positions, because the existing paste parser already knows how to
 * read space-aligned schedule rows. One parser, one set of behaviours.
 *
 * pdf.js is vendored under /static/vendor rather than loaded from a CDN so this
 * keeps working offline and behind corporate networks that block CDNs.
 */

let _lib = null;

async function lib() {
  if (_lib) return _lib;
  const mod = await import('/static/vendor/pdf.min.mjs');
  mod.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.mjs';
  _lib = mod;
  return mod;
}

/**
 * Rebuild one page's text lines from positioned glyph runs.
 *
 * Items come back in reading order but with no whitespace between columns — the
 * gap IS the column boundary. We group by baseline (y), sort across (x), then
 * translate each horizontal gap back into a proportional run of spaces so a row
 * reads as "ID   Name   Duration   Start   Finish".
 */
function itemsToLines(items) {
  const rows = new Map();
  for (const it of items) {
    if (!it.str || !it.str.trim()) continue;
    // 2pt bucket: glyphs on one baseline can wobble a fraction of a point
    const y = Math.round(it.transform[5] / 2) * 2;
    if (!rows.has(y)) rows.set(y, []);
    rows.get(y).push(it);
  }
  const out = [];
  // PDF origin is bottom-left, so descending y is top-to-bottom on the page
  for (const y of [...rows.keys()].sort((a, b) => b - a)) {
    const line = rows.get(y).sort((a, b) => a.transform[4] - b.transform[4]);
    let text = '', cursor = null;
    for (const it of line) {
      const x = it.transform[4];
      const charW = (it.width && it.str.length) ? (it.width / it.str.length) : 5;
      if (cursor !== null) {
        const gap = x - cursor;
        if (gap > charW * 0.5) {
          const spaces = Math.max(1, Math.round(gap / Math.max(charW, 1)));
          text += ' '.repeat(Math.min(spaces, 40));   // cap runaway indents
        }
      }
      text += it.str;
      cursor = x + (it.width || 0);
    }
    if (text.trim()) out.push(text.replace(/\s+$/, ''));
  }
  return out;
}

/**
 * Extract every page's text, in page order.
 * onProgress(done, total) is called per page so the UI can show a bar.
 * Returns {text, pages, chars, empty}.
 */
export async function extractPdfText(file, onProgress) {
  const pdfjs = await lib();
  const buf = await file.arrayBuffer();
  const doc = await pdfjs.getDocument({
    data: buf,
    // no network fetches for fonts/cmaps — everything stays local
    disableFontFace: true,
    isEvalSupported: false,
  }).promise;

  const lines = [];
  for (let p = 1; p <= doc.numPages; p++) {
    const page = await doc.getPage(p);
    const content = await page.getTextContent();
    lines.push(...itemsToLines(content.items));
    page.cleanup();
    if (onProgress) onProgress(p, doc.numPages);
  }
  const pages = doc.numPages;
  await doc.destroy();

  const text = lines.join('\n');
  return {
    text,
    pages,
    chars: text.length,
    // a scanned/photographed PDF has images but no text layer
    empty: text.replace(/\s/g, '').length < 20,
  };
}
