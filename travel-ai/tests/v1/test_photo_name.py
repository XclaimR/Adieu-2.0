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
