# Local Web UI

Launch `scripts/START_WEB_UI.cmd` or `python -m ru_en_dubber.web.server --open` from the checkout. The standard-library service binds to loopback. It catalogs managed channels/videos, starts workers, displays measured stage progress, and exposes run artifacts. Worker state belongs in `.runtime/runs/`, while durable video content stays under `channels/`. The current interface contains Russian labels. It is not a hosted multi-user service.

See [installation](installation.md), [usage](usage.md), and [release verification](release.md).
