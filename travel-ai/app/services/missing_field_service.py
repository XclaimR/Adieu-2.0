from app.schemas.traveler_profile import TravelerProfile


class MissingFieldService:

    REQUIRED_FIELDS = [
        "destination",
        "traveler_count",
        "budget",
        "duration"
    ]

    @classmethod
    def get_missing_fields(
        cls,
        profile: TravelerProfile
    ) -> list[str]:

        missing = []

        for field in cls.REQUIRED_FIELDS:

            value = getattr(profile, field)

            if value is None:
                missing.append(field)

        return missing