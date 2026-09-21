/* analytics.js — Google Analytics 4 initialisation, shared by every page.
 *
 * This is Google's standard gtag snippet with one change: it lives in a file
 * instead of an inline <script>. The reason is the CSP, which carries no
 * 'unsafe-inline' anywhere.
 *
 * Google recommends a nonce for the inline form. A nonce has to be minted per
 * request by a server, and this site is static files on a CDN — there is no
 * request to mint one. The other option is a sha256, which index.html already
 * uses for its own inline blocks and tools/check_csp_hashes.py maintains. But
 * that tooling reads index.html only, so the four error pages would carry a
 * hand-written hash nothing regenerates. Served from 'self', this file needs
 * neither, and all five pages share one copy of the measurement ID.
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
