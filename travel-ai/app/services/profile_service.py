from app.schemas.traveler_profile import TravelerProfile

class ProfileService:
    REQUIRED_FIELDS = [
        "destination",
        "traveler_count",
        "budget"
    ]
    
    @classmethod
    def get_missing_fields(cls, profile: TravelerProfile) -> list[str]:
        missing_fields = []
        for field in cls.REQUIRED_FIELDS:
            if getattr(profile, field) is None:
                missing_fields.append(field)
        return missing_fields
    
    