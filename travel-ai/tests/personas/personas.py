"""The six test travelers. Each one says only what is listed here; the runner checks the app made up nothing else.

facts: profile field -> values that count as "what the persona said". Fields a persona never mentions must stay
empty in the trip profile. `mood` lists word stems; every mood word the app stores must contain one of them.
answers: (words in the app's question, reply, facts that reply states). Each answer is used at most once.
chips: if no answer fits, tap the first chip containing one of these words; otherwise say "just show me plans".
banned: words that must not appear in draft/pick names and notes. banned_tiers: stay price tiers that don't fit.
"""

WHERE = ("where", "destination")
MOOD = ("kind of trip", "vibe", "mood", "feel", "style", "type of trip", "dreaming")
WHO = ("who", "travel with", "travelling with", "traveling with", "coming")
HOW_LONG = ("how long", "how many days", "days", "nights", "length")
BUDGET = ("budget", "spend", "price")
WHEN = ("when", "dates", "month")

NOT_SURE_DATES = (WHEN, "Not sure about dates yet.", {})

PERSONAS = [
    {
        "name": "bachelor",
        "opening": "Planning a fun, lively bachelor trip to Japan with a group of friends!",
        "facts": {
            "destination": ["Japan"], "party": ["friends"],
            "mood": ["bachelor", "party", "celebrat", "nightlife", "fun", "lively", "social", "adventur", "food"],
        },
        "answers": [
            (MOOD, "Fun and lively: nightlife, food and a bit of adventure.", {}),
            (HOW_LONG, "About 6 days.", {"duration_days": [6]}),
            (BUDGET, "Mid-range is fine.", {"budget_level": ["mid"]}),
            NOT_SURE_DATES,
        ],
        "chips": ["friends", "adventur", "nightlife"],
        "banned": ["romantic", "couples", "honeymoon", "kids", "family-friendly"],
        "banned_tiers": [],
    },
    {
        "name": "lisbon_couple",
        "opening": "My wife and I want a slow food week in Lisbon.",
        "facts": {
            "destination": ["Lisbon"], "party": ["couple"], "party_size": [2], "duration_days": [7],
            "mood": ["food", "slow", "relax", "culinar", "gastro", "leisur", "romantic"],
        },
        "answers": [
            (BUDGET, "Mid-range.", {"budget_level": ["mid"]}),
            NOT_SURE_DATES,
        ],
        "chips": ["partner", "foodie", "relax"],
        "banned": ["bar crawl", "pub crawl", "nightclub", "kids", "playground"],
        "banned_tiers": [],
    },
    {
        "name": "family_beach",
        "opening": "We want a beach holiday with my husband and our two kids.",
        "facts": {
            "party": ["family"], "kids": [True], "party_size": [4],
            "mood": ["beach", "relax", "family", "sun", "fun", "kid", "sea"],
        },
        "answers": [
            (WHERE, "Somewhere in the Algarve, Portugal.", {"destination": ["Algarve"]}),
            (HOW_LONG, "A week.", {"duration_days": [7]}),
            NOT_SURE_DATES,
        ],
        "chips": ["family", "kids", "warm", "relax"],
        "banned": ["nightlife", "nightclub", "bar crawl", "pub crawl", "casino", "romantic", "adults only",
                   "adults-only", "cocktail bar"],
        "banned_tiers": [],
    },
    {
        "name": "solo_hiker",
        "opening": "Going solo on a hiking trip in the Dolomites for 5 days.",
        "facts": {
            "destination": ["Dolomites"], "party": ["solo"], "party_size": [1], "duration_days": [5],
            "mood": ["hik", "nature", "adventur", "outdoor", "active", "mountain", "trek"],
        },
        "answers": [NOT_SURE_DATES],
        "chips": ["just me", "adventur", "mountain"],
        "banned": ["romantic", "couples", "honeymoon", "kids", "family-friendly"],
        "banned_tiers": [],
    },
    {
        "name": "budget_backpacker",
        "opening": "Backpacking through Vietnam on a tight budget.",
        "facts": {
            "destination": ["Vietnam"], "budget_level": ["budget"],
            "mood": ["adventur", "backpack", "budget", "explor", "culture", "food", "street"],
        },
        "answers": [
            (MOOD, "Adventurous: street food and exploring.", {}),
            (WHO, "Just me.", {"party": ["solo"], "party_size": [1]}),
            (HOW_LONG, "Two weeks.", {"duration_days": [14]}),
            NOT_SURE_DATES,
        ],
        "chips": ["just me", "adventur"],
        "banned": ["luxury", "five-star", "5-star", "michelin"],
        "banned_tiers": ["luxury"],
    },
    {
        "name": "luxury_weekend",
        "opening": "A luxury weekend in Paris with my partner.",
        "facts": {
            "destination": ["Paris"], "party": ["couple"], "party_size": [2], "budget_level": ["luxury"],
            "duration_days": [2, 3],
            "mood": ["luxur", "romantic", "indulg", "relax", "fine", "elegant", "pamper", "upscale"],
        },
        "answers": [
            (MOOD, "Romantic and indulgent: fine dining and a nice spa.", {}),
            NOT_SURE_DATES,
        ],
        "chips": ["partner", "romantic"],
        "banned": ["hostel", "backpacker", "budget-friendly"],
        "banned_tiers": ["budget"],
    },
]
