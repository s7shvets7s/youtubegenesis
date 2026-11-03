from __future__ import annotations

import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

from ru_en_dubber.visuals.models import AssetCandidate, VisualRequest
from ru_en_dubber.visuals.planner import parse_plan, VisualPlanner, ground_generated_plan
from ru_en_dubber.visuals.policy import rejection_reason, rank, route, rights_reason
from ru_en_dubber.visuals.providers import PexelsProvider, PixabayProvider, WikimediaProvider, LocProvider, NaraProvider
from ru_en_dubber.visuals.transport import Transport, ProviderError, SafeRedirect, download
from ru_en_dubber.visuals.engine import validate_external


def request(**kwargs):
    return replace(VisualRequest(1, 0, 5, "A factory produces steel", "steel factory",
        visual_intent="process", accuracy="illustrative"), **kwargs)


def asset(**kwargs):
    return replace(AssetCandidate("wikimedia", "1", "https://upload.wikimedia.org/a.jpg",
        "https://commons.wikimedia.org/wiki/File:A.jpg", author="Author", media_type="image",
        title="steel factory", license_code="CC BY 4.0", rights_evidence="Attribution",
        license_url="https://creativecommons.org/licenses/by/4.0/", width=1920, height=1080), **kwargs)


class ProviderContracts(unittest.TestCase):
    def test_pexels_video_and_photo_normalization(self):
        transport = Mock()
        transport.json.side_effect = [
            {"videos": [{"id": 12, "url": "https://www.pexels.com/video/12/", "duration": 8,
                "user": {"name": "Person"}, "video_files": [{"file_type": "video/mp4", "width": 1920, "height": 1080,
                    "link": "https://videos.pexels.com/a.mp4"}]}]},
            {"photos": [{"id": 13, "width": 2000, "height": 1500, "src": {"large2x": "https://images.pexels.com/a.jpg"},
                "url": "https://www.pexels.com/photo/13/", "photographer": "Person"}]}]
        values = PexelsProvider(transport, "test").search(request())
        self.assertEqual([v.media_type for v in values], ["video", "image"])
        self.assertTrue(all(v.license_code == "Pexels" for v in values))
        self.assertTrue(all(v.retrieved_at for v in values))

    def test_pixabay_malformed_items_do_not_hide_valid_result(self):
        transport = Mock()
        transport.json.return_value = {"hits": [{"id": "broken"}, {"id": 20, "pageURL": "https://pixabay.com/videos/20/",
            "videos": {"medium": {"url": "https://cdn.pixabay.com/a.mp4", "width": 1920, "height": 1080}},
            "duration": 5, "tags": "steel factory"}]}
        values = PixabayProvider(transport, "test").search(request(media_types=("video",)))
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].license_code, "Pixabay")
        self.assertEqual(transport.json.call_args.args[2]["safesearch"], "true")

    def test_commons_preserves_license_author_date_and_description(self):
        transport = Mock()
        transport.json.return_value = {"query": {"pages": {"1": {"pageid": 1, "title": "File:Factory.jpg", "imageinfo": [{
            "url": "https://upload.wikimedia.org/a.jpg", "mime": "image/jpeg", "width": 1000, "height": 800,
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Factory.jpg", "extmetadata": {
                "Artist": {"value": "<a>Author</a>"}, "LicenseShortName": {"value": "CC BY 4.0"},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                "UsageTerms": {"value": "Attribution"}, "DateTimeOriginal": {"value": "1968-05-01"},
                "ImageDescription": {"value": "A steel factory in Vietnam"}}}]}}}}
        value = WikimediaProvider(transport).search(request())[0]
        self.assertEqual(value.author, "Author")
        self.assertEqual(value.year_from, 1968)
        self.assertIsNone(rights_reason(value))

    def test_nara_schema_and_unrestricted_is_not_public_domain(self):
        transport = Mock()
        transport.json.return_value = {"body": {"hits": {"hits": [{"_id": "100", "_source": {"record": {
            "title": "Vietnam patrol", "useRestriction": {"status": "Unrestricted"},
            "productionDates": [{"year": 1968}], "digitalObjects": [{"objectId": "101",
            "objectUrl": "https://catalog.archives.gov/a.mp4"}]}}}]}}}
        provider = NaraProvider(transport, "test")
        self.assertFalse(provider.enabled)
        value = provider.parse_response(transport.json.return_value, request())[0]
        self.assertEqual(value.year_from, 1968)
        self.assertEqual(value.license_code, "unknown")
        self.assertEqual(value.page_url, "https://catalog.archives.gov/id/100")

    def test_loc_follows_item_resources_and_does_not_accept_negated_pd(self):
        transport = Mock()
        transport.json.side_effect = [{"results": [{"id": "https://www.loc.gov/item/100/"}]}, {
            "item": {"title": "Factory", "rights": "This is not public domain", "date": "1930"},
            "resources": [{"files": [[{"url": "https://tile.loc.gov/a.jpg", "width": 1000, "height": 800}]]}]}]
        value = LocProvider(transport).search(request())[0]
        self.assertEqual(value.license_code, "unknown")
        self.assertEqual(value.year_from, 1930)

    def test_missing_stock_keys_disable_only_stock_adapters(self):
        for cls in (PexelsProvider, PixabayProvider, NaraProvider):
            self.assertFalse(cls(Mock()).enabled)
        self.assertTrue(WikimediaProvider(Mock()).enabled)


class PolicyTests(unittest.TestCase):
    def test_exact_event_rejects_stock_wrong_period_and_unknown_period(self):
        req = request(subject="Vietnam patrol", visual_intent="event", accuracy="exact",
                      entities=("Vietnam",), location="Vietnam", year_from=1965, year_to=1973)
        good = asset(title="Vietnam patrol", year_from=1968, year_to=1968)
        self.assertIsNone(rejection_reason(good, req))
        self.assertEqual(rejection_reason(replace(good, year_from=2020, year_to=2020), req), "period_mismatch")
        self.assertEqual(rejection_reason(replace(good, year_from=None), req), "period_unknown")
        self.assertEqual(rejection_reason(replace(good, provider="pexels", license_code="Pexels"), req), "stock_cannot_verify_exact_claim")

    def test_unknown_rights_and_missing_attribution_fail_before_quality_ranking(self):
        self.assertEqual(rejection_reason(asset(license_code="unknown", width=7680), request()), "rights_require_review")
        self.assertEqual(rejection_reason(asset(author=""), request()), "missing_attribution")
        self.assertEqual(rejection_reason(asset(license_code="CC BY-SA 4.0"), request()), "rights_require_review")

    def test_diversity_and_deterministic_sort(self):
        first, second = asset(), asset(asset_id="2", download_url="https://upload.wikimedia.org/b.jpg")
        self.assertEqual(rank([first, second], request(), {first.key}, 1920, 1080)[0], second)
        self.assertEqual(rank([first, second], request(), set(), 1920, 1080), rank([second, first], request(), set(), 1920, 1080))

    def test_router_uses_visual_intent_not_domain(self):
        providers = [PexelsProvider(Mock(), "test"), WikimediaProvider(Mock())]
        self.assertEqual(route(providers, request(domain="history"))[0].name, "pexels")
        self.assertEqual(route(providers, request(domain="economics", accuracy="exact"))[0].name, "wikimedia")

    def test_number_never_uses_stock(self):
        self.assertEqual(rejection_reason(asset(), request(visual_intent="number")), "requires_data_or_explicit_plan")


class PlannerTests(unittest.TestCase):
    def test_neighboring_date_and_generic_location_are_removed(self):
        value = {"subject": "steel factory", "entities": ["1968"], "visual_intent": "process", "accuracy": "illustrative",
                 "location": "modern factories", "year_from": 1968, "media_types": ["video", "chart"]}
        result = ground_generated_plan(value, request())
        self.assertIsNone(result["year_from"])
        self.assertIsNone(result["location"])
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["media_types"], ["video"])

    def test_schema_rejects_commands_and_invalid_period(self):
        value = {"subject": "factory", "entities": [], "visual_intent": "process", "accuracy": "illustrative"}
        self.assertEqual(parse_plan(value, request()).subject, "factory")
        for bad in (dict(value, command="calc.exe"), dict(value, year_from=2020, year_to=1900), dict(value, entities="factory")):
            with self.assertRaises(ValueError):
                parse_plan(bad, request())

    def test_unknown_intent_cannot_turn_into_automatic_broll(self):
        value = {"subject": "factory", "entities": [], "visual_intent": "arbitrary", "accuracy": "illustrative"}
        parsed = parse_plan(value, request())
        self.assertEqual((parsed.visual_intent, parsed.accuracy), ("abstract", "exact"))


class TransportTests(unittest.TestCase):
    def test_redirect_rejected_before_second_request(self):
        redirect = SafeRedirect(frozenset({"videos.pexels.com"}))
        with self.assertRaisesRegex(RuntimeError, "allowed HTTPS"):
            redirect.redirect_request(Request("https://videos.pexels.com/a.mp4"), None, 302, "", {}, "https://evil.example/a.mp4")

    def test_cache_hides_keys_and_avoids_duplicate_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(Path(tmp))
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b'{"hits": []}'
            opener = Mock()
            opener.open.return_value = response
            with patch("ru_en_dubber.visuals.transport.build_opener", return_value=opener):
                for _ in range(2):
                    transport.json("pixabay", "https://pixabay.com/api/", {"q": "factory", "key": "SECRET"})
            self.assertEqual(opener.open.call_count, 1)
            self.assertEqual(transport.cache_hits, 1)
            self.assertNotIn("SECRET", repr(list(Path(tmp).rglob("*"))))
            self.assertNotIn("SECRET", next(Path(tmp).rglob("*.json")).read_text())

    def test_rate_limit_cooldown_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = Transport(Path(tmp), budget=2)
            opener = Mock()
            opener.open.side_effect = HTTPError("https://pixabay.com", 429, "limit", {"Retry-After": "60"}, io.BytesIO())
            with patch("ru_en_dubber.visuals.transport.build_opener", return_value=opener):
                for _ in range(2):
                    with self.assertRaises(ProviderError):
                        transport.json("pixabay", "https://pixabay.com/api/", {"q": "factory"})
            self.assertEqual(opener.open.call_count, 1)

    def test_html_disguised_as_image_is_rejected_and_partial_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.headers.get_content_type.return_value = "image/jpeg"
            response.headers.get.return_value = "0"
            response.read.side_effect = [b"<html>wrong</html>", b""]
            opener = Mock()
            opener.open.return_value = response
            path = Path(tmp) / "image"
            with patch("ru_en_dubber.visuals.transport.build_opener", return_value=opener):
                with self.assertRaises(ProviderError):
                    download("https://upload.wikimedia.org/a.jpg", path, frozenset({"upload.wikimedia.org"}), "image")
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".part").exists())

    def test_still_image_and_forbidden_stream_validation(self):
        value = {"streams": [{"codec_type": "video", "codec_name": "png", "width": 1000, "height": 800}],
                 "format": {"format_name": "png_pipe"}}
        validate_external(value, "image")
        value["streams"].append({"codec_type": "data", "codec_name": "tmcd"})
        with self.assertRaises(ProviderError):
            validate_external(value, "image")


if __name__ == "__main__":
    unittest.main()
