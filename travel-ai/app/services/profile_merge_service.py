from app.schemas.traveler_profile import TravelerProfile


class ProfileMergeService:

    @staticmethod
    def merge(
        existing: TravelerProfile,
        delta: TravelerProfile
    ) -> TravelerProfile:

        merged = existing.model_dump()

        for field, value in delta.model_dump().items():

            if value is not None:
                merged[field] = value

        return TravelerProfile(**merged)