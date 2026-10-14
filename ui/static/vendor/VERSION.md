# Vendored front-end libraries

No CDN: the app serves only files in `ui/`. Each vendored file is the unmodified build
from its package.

| File | Library | Version | Licence | Source | SHA-256 of the file |
|---|---|---|---|---|---|
| `chart.umd.min.js` | Chart.js | 4.5.1 | MIT (`CHARTJS_LICENSE.md`) | npm package `chart.js@4.5.1`, `dist/chart.umd.min.js` (tarball SHA-1 `19dd1a9a386a3f6397691672231cb5fc9c052c35`, equal to the registry's `dist.shasum`) | `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a` |

To upgrade: `npm pack chart.js@<version>` in an empty folder, copy `package/dist/chart.umd.min.js`
and `package/LICENSE.md` here, and update this table. `tests/test_web_app.py` checks the
hash above against the file.
