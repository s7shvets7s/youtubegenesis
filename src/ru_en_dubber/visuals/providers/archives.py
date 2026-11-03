from __future__ import annotations

from ..models import VisualRequest, ProviderCapabilities
from ..transport import ProviderError
from .base import BaseProvider, media_kind, plain, years


class WikimediaProvider(BaseProvider):
    name = "wikimedia"
    capabilities = ProviderCapabilities(factual_metadata=True)
    archive = True
    media_hosts = frozenset({"upload.wikimedia.org"})

    def search(self, request: VisualRequest):
        data = self.transport.json(self.name, "https://commons.wikimedia.org/w/api.php", {
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": request.subject, "gsrlimit": self.limit, "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920})
        results = []
        for page in data.get("query", {}).get("pages", {}).values():
            try:
                info = page["imageinfo"][0]
                meta = info.get("extmetadata", {})
                value = lambda name: plain(meta.get(name, {}).get("value", ""))
                mime = info.get("mime", "")
                kind = "image" if mime in {"image/jpeg", "image/png"} else "video" if mime in {"video/webm", "video/ogg", "video/mp4"} else None
                if kind not in request.media_types:
                    continue
                year_from, year_to = years(value("DateTimeOriginal"))
                code = value("LicenseShortName")
                code = "CC0" if code.upper().startswith("CC0") else code
                results.append(self.candidate(asset_id=str(page["pageid"]),
                    download_url=info.get("thumburl", info["url"]) if kind == "image" else info["url"],
                    page_url=info.get("descriptionurl", ""), author=value("Artist"),
                    width=int(info.get("thumbwidth", info.get("width", 0))),
                    height=int(info.get("thumbheight", info.get("height", 0))), media_type=kind,
                    title=plain(page["title"]), description=value("ImageDescription"),
                    license_code=code, license_url=value("LicenseUrl"),
                    rights_evidence=value("UsageTerms"), year_from=year_from, year_to=year_to))
            except (KeyError, TypeError, ValueError, IndexError, AttributeError):
                continue
        return results


class NaraProvider(BaseProvider):
    name = "nara"
    capabilities = ProviderCapabilities(factual_metadata=True, geographic_focus=("United States", "US Army", "American"))
    archive = True
    media_hosts = frozenset({"catalog.archives.gov"})
    disabled_reason = "NARA API terms prohibit caching/storing returned content; production use deferred"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled = False

    def search(self, request: VisualRequest):
        raise ProviderError(self.disabled_reason)

    def parse_response(self, data: dict, request: VisualRequest):
        """Normalize the documented v2 schema; live fetching is deferred by service terms."""
        results = []
        for hit in data.get("body", {}).get("hits", {}).get("hits", [])[:self.limit]:
            try:
                record = hit["_source"]["record"]
                restrictions = record.get("useRestriction", {})
                evidence = plain(restrictions)
                # Access or 'Unrestricted' alone is not a copyright grant.
                code = "unknown"
                start, end = years(record.get("productionDates", []))
                for obj in record.get("digitalObjects", [])[:self.limit]:
                    url = obj.get("objectUrl", "")
                    kind = media_kind(url)
                    if kind not in request.media_types:
                        continue
                    results.append(self.candidate(asset_id=str(obj.get("objectId", hit["_id"])),
                        download_url=url, page_url=f"https://catalog.archives.gov/id/{hit['_id']}",
                        media_type=kind, title=plain(record.get("title")),
                        description=plain(record.get("scopeAndContentNote")),
                        author=plain(record.get("creators", "")), year_from=start, year_to=end,
                        license_code=code, rights_evidence=evidence,
                        license_url=f"https://catalog.archives.gov/id/{hit['_id']}"))
            except (KeyError, ValueError, TypeError):
                continue
        return results


class LocProvider(BaseProvider):
    name = "loc"
    capabilities = ProviderCapabilities(factual_metadata=True, geographic_focus=("United States", "American"))
    archive = True
    media_hosts = frozenset({"www.loc.gov", "loc.gov", "tile.loc.gov", "cdn.loc.gov", "download.loc.gov"})

    def search(self, request: VisualRequest):
        data = self.transport.json(self.name, "https://www.loc.gov/search/",
                                   {"fo": "json", "q": request.subject, "c": self.limit})
        results = []
        for item in data.get("results", [])[:self.limit]:
            try:
                page = item.get("id", "")
                if not page.startswith("https://www.loc.gov/item/"):
                    continue
                detail = self.transport.json(self.name, page, {"fo": "json"})
                record = detail.get("item", {})
                evidence = plain(record.get("rights", "")) + " " + plain(record.get("rights_advisory", ""))
                # Preserve ambiguous rights for review; never infer PD from archive membership.
                # Only an unambiguous statement is automated; lengthy rights advisories need review.
                code = "Public domain" if evidence.strip().lower().rstrip(".") in {
                    "public domain", "this item is in the public domain"
                } else "unknown"
                start, end = years(record.get("date", ""))
                for resource in detail.get("resources", [])[:3]:
                    for group in resource.get("files", [])[:3]:
                        entries = group if isinstance(group, list) else [group]
                        for file in entries:
                            url = file.get("url", "")
                            kind = media_kind(url)
                            if kind not in request.media_types:
                                continue
                            results.append(self.candidate(asset_id=page + "#" + url,
                                download_url=url, page_url=page, media_type=kind,
                                title=plain(record.get("title")), description=plain(record.get("description")),
                                width=int(file.get("width") or 0), height=int(file.get("height") or 0),
                                author=plain(record.get("contributor_names", "")),
                                license_code=code, license_url=page, rights_evidence=evidence,
                                year_from=start, year_to=end))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        return results
