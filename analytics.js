/* analytics.js — Google Analytics 4 initialisation for the ERROR PAGES.
 *
 * index.html does not use this file: it carries Google's inline snippet,
 * hashed into its CSP by tools/check_csp_hashes.py. That is deliberate —
 * Google verifies the tag by rendering the page, and anything that only reads
 * the HTML needs to find gtag('config') there rather than behind a fetch.
 *
 * The error pages cannot do that. This is Google's standard snippet moved
 * into a file, because the CSP carries no 'unsafe-inline' anywhere.
 *
 * Google recommends a nonce for the inline form. A nonce has to be minted per
 * request by a server, and this site is static files on a CDN — there is no
 * request to mint one. The other option is a sha256, which index.html already
 * uses for its own inline blocks and tools/check_csp_hashes.py maintains. But
 * that tooling reads index.html only, so the error pages would carry a
 * hand-written hash nothing regenerates. Served from 'self', this file needs
 * neither.
 *
 * robots.txt must keep allowing /analytics.js. It is in an allowlist that ends
 * in Disallow: /, and while this file was behind that rule Google could not
 * render the tag at all.
 *
 * Load order matters and is handled in the markup: this file is
 * render-blocking, the gtag/js tag next to it is async, so dataLayer and
 * gtag() always exist before Google's script runs.
 *
 * Open item: there is no consent gate. Google requires a certified consent
 * platform before analytics and ads may be collected from EEA/UK visitors —
 * the "Politica de cookies" entry in todo.txt. Consent Mode would be wired
 * here, ahead of the config call.
 */
window.dataLayer = window.dataLayer || [];
function gtag() { dataLayer.push(arguments); }
gtag('js', new Date());
gtag('config', 'G-QV4DCF3HTN');
