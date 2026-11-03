from __future__ import annotations

from ..models import VisualRequest, ProviderCapabilities
from .base import BaseProvider


class PexelsProvider(BaseProvider):
    name = "pexels"
    capabilities = ProviderCapabilities(licenses=("Pexels",))
    media_hosts = frozenset({"videos.pexels.com", "images.pexels.com"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled = bool(self.key)

    def search(self, request: VisualRequest):
        results = []
        for kind in request.media_types:
            if kind not in {"video", "image"}:
                continue
            endpoint = "https://api.pexels.com/" + ("videos/search" if kind == "video" else "v1/search")
            data = self.transport.json(self.name, endpoint,
                {"query": request.subject, "per_page": self.limit, "orientation": "landscape"},
                headers={"Authorization": self.key})
            for item in data.get("videos" if kind == "video" else "photos", []):
                try:
                    if kind == "video":
                        files = [f for f in item.get("video_files", []) if f.get("file_type") == "video/mp4"]
                        # Prefer a useful 1080p rendition instead of always downloading 4K.
                        file = min(files, key=lambda f: abs(int(f.get("width") or 0) - 1920))
                        url, width, height = file["link"], int(file["width"]), int(file["height"])
                        author = item.get("user", {}).get("name", "")
                    else:
                        url = item["src"]["large2x"]
                        width, height = int(item["width"]), int(item["height"])
                        author = item.get("photographer", "")
                    results.append(self.candidate(asset_id=str(item["id"]), download_url=url,
                        page_url=item.get("url", ""), author=author, width=width, height=height,
                        duration=float(item.get("duration") or 0), media_type=kind,
                        title=item.get("alt", ""), license_code="Pexels",
                        license_url="https://www.pexels.com/license/", rights_evidence="Pexels API content license"))
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
        return results


class PixabayProvider(BaseProvider):
    name = "pixabay"
    capabilities = ProviderCapabilities(licenses=("Pixabay",))
    media_hosts = frozenset({"cdn.pixabay.com", "pixabay.com"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled = bool(self.key)

    def search(self, request: VisualRequest):
        results = []
        for kind in request.media_types:
            if kind not in {"video", "image"}:
                continue
            endpoint = "https://pixabay.com/api/" + ("videos/" if kind == "video" else "")
            data = self.transport.json(self.name, endpoint,
                {"key": self.key, "q": request.subject[:100], "per_page": max(3, self.limit), "safesearch": "true"})
            for item in data.get("hits", []):
                try:
                    if kind == "video":
                        files = [f for f in item["videos"].values() if f.get("url")]
                        file = min(files, key=lambda f: abs(int(f.get("width") or 0) - 1920))
                        url, width, height = file["url"], int(file["width"]), int(file["height"])
                    else:
                        url, width, height = item["largeImageURL"], int(item["imageWidth"]), int(item["imageHeight"])
                    results.append(self.candidate(asset_id=str(item["id"]), download_url=url,
                        page_url=item["pageURL"], author=item.get("user", ""), width=width, height=height,
                        duration=float(item.get("duration") or 0), media_type=kind, title=item.get("tags", ""),
                        license_code="Pixabay", license_url="https://pixabay.com/service/license-summary/",
                        rights_evidence="Pixabay API Content License"))
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
        return results
