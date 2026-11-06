# Local financial animation extension

`kit/engine/market2d.js` is an original local addition, separate from the supplied upstream kit.
Import it in a scene with `import * as M from '/engine/market2d.js'`. It requires the existing
`core.js` style and drawing helpers. No additional dependency, network request or trading service.
All amounts, financial claims and labels are authored by the scene; prices are illustrations.

- `MARKET_CAST`, `portrait`, `character`: three consistent cartoon identities, facial expressions,
  blinking, breathing, gestures and phone/point/shrug/celebrate poses. Trump is a stylized
  caricature, not a likeness generated from a face-recognition system.
- `priceSeries(seed, count, fall)`: deterministic OHLC illustration with adjacent open/close
  continuity; positive close floor. `candleChart` reveals a supplied or generated series over
  `duration`, with explicit heading, displayed value and illustrative label.
- `coin`: face coin or dollar coin, optional spin. `postCard`: clearly fictional post layout.
- `moneyFlow`, `balanceCard`, `countdown`: animated transfers and explicit labeled financial
  categories. The caller is responsible for accounting semantics and avoiding double counting.
- `globe`, `office`, `skyline`, `particles`, `documentCard`: scene vocabulary in the same palette.
- `panel`, `label`, `fitLabel`: layout and width-constrained typography.

Draw functions accept a CanvasRenderingContext2D plus the options shown in source. Coordinates
are in the existing 1920x1080 scene space; `t` is deterministic scene-local seconds. Do not use
wall-clock time, random seeds that change per frame, live prices or external media in a render.

Small check: `node --test kit/engine/market2d.test.mjs` from the skill directory. The check covers
seed repeatability, OHLC geometry/continuity, directional reversal and distinct character
palettes. It does not replace rendered visual QC. The Trump film separately exercises all
20 scene modes through a 247-shot contact sheet and full-length render.

The canonical extension lives in the vendored kit. Copy this new module into an existing
`.tools/2d-video/engine/` runtime without replacing its unrelated files or local patches.
