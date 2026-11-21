import pytest

from app.services.places_service import PlacesService


@pytest.mark.parametrize("name", [
    "places/a/photos/../../../x",
    "places/a/photos/b%23",
    "places/a/photos/b/../c",
    "other/a/photos/b",
])
def test_photo_url_rejects_names_outside_the_photo_shape(name):
    # Validation runs before the cache or network, so no db is needed.
    with pytest.raises(ValueError):
        PlacesService(db=None).photo_url(name)


class _Resp:
    def __init__(self, status_code, data=None):
        self.status_code, self._data = status_code, data or {}

    def json(self):
        return self._data


def test_expired_photo_name_falls_back_to_the_place_current_photo(db_session, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("places/p1/photos/old/media"):
            return _Resp(400)  # expired name
        if url.endswith("places/p1"):
            return _Resp(200, {"photos": [{"name": "places/p1/photos/new"}]})
        return _Resp(200, {"photoUri": "https://img/new"})

    monkeypatch.setattr("app.services.places_service.httpx.get", fake_get)
    assert PlacesService(db_session).photo_url("places/p1/photos/old") == "https://img/new"
    assert calls[-1].endswith("places/p1/photos/new/media")


def test_quota_errors_do_not_trigger_a_fallback_lookup(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.places_service.httpx.get", lambda url, **kw: calls.append(url) or _Resp(429))
    with pytest.raises(Exception):
        PlacesService(db_session).photo_url("places/p1/photos/old")
    assert len(calls) == 1
