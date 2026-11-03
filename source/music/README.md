# Local music library

Add your own audio under this directory and describe each track in `catalog.json`.
Audio and downloaded Jamendo tracks stay local. Only the empty catalog schema and
this documentation belong in the public repository. A populated catalog can also
contain private paths and attribution; review it before committing anything.

Example entry for a track you own:

```json
{
  "id": "example-ambient",
  "file": "example-ambient.mp3",
  "title": "Example Ambient",
  "artist": "Example Artist",
  "provider": "local",
  "provider_id": null,
  "genres": ["ambient"],
  "moods": ["calm"],
  "energy": "low",
  "instrumental": true,
  "use_for": ["explanation"],
  "avoid_for": ["conflict"],
  "license": "owned",
  "source_url": null,
  "attribution": null,
  "approved": true
}
```

Allowed energy values: `very_low`, `low`, `medium`, `high`, `very_high`.
Only existing approved files with explicit license evidence are eligible. Jamendo
fallback retains downloaded files, attribution, and hashes for later local reuse.
