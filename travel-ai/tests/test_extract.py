from app.services.profile_extraction_service import ProfileExtractionService


def test_extract():
    user_message = "I want to plan a trip for 2 people to Paris next month. Our budget is around $3000 and we prefer a relaxed travel style. We are celebrating our anniversary, so we want to make it special."
    profile = ProfileExtractionService.extract(user_message)
    print(profile.model_dump_json(indent=2))
    assert profile.traveler_count == 2
    assert profile.destination == "Paris"
    assert profile.budget == 3000
    assert profile.travel_style == "relaxed"
    assert profile.occasion == "anniversary"
    
    user_message = "I want a luxury Japan trip for my wife and I."
    profile = ProfileExtractionService.extract(user_message)
    print(profile.model_dump_json(indent=2))
    
    user_message = "Family of four going to Italy. Budget is 8000 dollars."
    profile = ProfileExtractionService.extract(user_message)
    print(profile.model_dump_json(indent=2))
    
    user_message = "Planning a honeymoon in Bali."
    profile = ProfileExtractionService.extract(user_message)
    print(profile.model_dump_json(indent=2))
    