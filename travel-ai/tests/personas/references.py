"""One human-written itinerary per persona (Ticket #67), approved by the owner on 2026-11-25.

Our own short summaries, never the authors' text: the link, the shape of each day in a few words, the must-sees
(as patterns that match a stop's name, so spellings like Todai-ji and Tōdai-ji both count) and the practical tips.
bachelor, family_beach and unsure have none: no human plan worth judging against was found, or the app picks the place.
"""

REFERENCES = {
    "first_timer_japan": {
        "url": "https://www.insidekyoto.com/2-week-japan-itinerary-the-grand-tour",
        "days": 14,  # the persona's 12 days are compared with its first 12
        "outline": ["Arrive in Tokyo", "Modern west Tokyo", "Traditional east Tokyo", "Train to Kyoto",
                    "Southern Higashiyama temples", "Day trip to Nara", "Arashiyama", "Kurama and Kibune",
                    "Train to Osaka", "Osaka", "Train to Hiroshima", "Miyajima", "Back to Tokyo", "Fly home"],
        "must_sees": [r"t[oō]dai-?ji|nara", r"arashiyama|bamboo", r"higashiyama|kiyomizu", r"kinkaku",
                      r"d[oō]tonbori", r"peace memorial", r"itsukushima|miyajima", r"kurama"],
        "rest_days": [],
        "tips": ["Buy shinkansen tickets online ahead", "Nara is a 30-minute train from Kyoto",
                 "Start early in Hiroshima", "Weigh a rail pass against single tickets"],
    },
    "bali_wellness": {
        "url": "https://www.alajode.com/?p=83707",
        "days": 10,
        "outline": ["Arrive, settle in Ubud", "Ubud yoga and ridge walk", "Sidemen rice terraces", "Slow day in Sidemen",
                    "Canggu surf and cafés", "Canggu sunset", "Uluwatu beaches", "Uluwatu temple at sunset",
                    "Easy Seminyak day", "Massage and a beach sunset"],
        "must_sees": [r"batur", r"campuhan", r"tanah lot", r"uluwatu", r"padang", r"echo beach", r"sidemen",
                      r"yoga barn|yoga"],
        "rest_days": [4, 9],  # the author's slow days
        "tips": ["Walk Campuhan Ridge early, before the heat", "Climb Mount Batur before dawn for sunrise",
                 "Tanah Lot near sunset at low tide", "The Kecak dance at Uluwatu is at sunset"],
    },
    "rome_family": {
        "url": "https://mamalovesrome.com/rome-itinerary-for-families",
        "days": 5,
        "outline": ["Colosseum and Forum", "Pantheon, a kids' class, Trastevere", "Vatican, castle and Navona",
                    "Spanish Steps and a children's museum", "Borghese and Testaccio"],
        "must_sees": [r"colosseum|colosseo", r"forum|foro", r"trevi", r"pantheon", r"vatican|st\.? peter",
                      r"sant'? ?angelo", r"navona", r"spanish steps|piazza di spagna", r"borghese", r"explora"],
        "rest_days": [],
        "tips": ["Book the Colosseum ahead", "A kids' tour of the Vatican Museums",
                 "Go to the Spanish Steps early", "Cover shoulders for the basilica"],
    },
    "lisbon_couple": {
        "url": "https://www.insidehook.com/travel/spend-7-perfect-days-lisbon",
        "days": 7,
        "outline": ["Chiado and a river picnic", "Belém and its pastries", "Beach, then a fado night",
                    "Tiles, snails and the castle", "Sintra palaces", "LX Factory and cocktails", "A museum and a river dinner"],
        "must_sees": [r"bel[eé]m tower|torre de bel", r"jer[oó]nimos", r"pena", r"sintra", r"s[aã]o jorge",
                      r"azulejo|tile museum", r"past[eé]is de bel", r"fado"],
        "rest_days": [],
        "tips": ["Go to Sintra early or late", "Take the pastéis away rather than queue", "Book fado ahead"],
    },
    "solo_hiker": {
        "url": "https://www.earthtrekkers.com/dolomites-itinerary/",
        "days": 5,
        "outline": ["Arrive, an afternoon hike from Cortina", "A big eastern hike", "Another eastern hike",
                    "Drive the passes to Val Gardena", "A western hike"],
        "must_sees": [r"tre cime", r"braies", r"sorapis", r"cinque torri", r"seceda|puez", r"siusi|seiser",
                      r"funes|villn", r"lagazuoi|tofana"],
        "rest_days": [],
        "tips": ["Book Tre Cime road parking online", "Reach Lago di Braies before 9 am in summer",
                 "Start Sorapis by 9 am", "Check cable car dates in autumn"],
    },
    "budget_backpacker": {
        "url": "https://www.wheregoesrose.com/vietnam-itinerary-2-weeks/",
        "days": 14,
        "outline": ["Hanoi Old Quarter", "Hanoi temples and lakes", "Ha Long Bay cruise", "Back, night train",
                    "Sapa villages", "Sapa trek", "Hoi An", "Cooking class and beach", "My Son or the islands",
                    "Lanterns and villages", "To Saigon", "Saigon", "Mekong or Cu Chi", "Saigon food"],
        "must_sees": [r"ho[aà]n ki[eế]m", r"old quarter", r"ha ?long", r"sapa|sa pa", r"h[oộ]i an",
                      r"covered bridge|japanese bridge", r"my son|mỹ sơn", r"b[eế]n th[aà]nh", r"war remnants",
                      r"c[uủ] chi"],
        "rest_days": [],
        "tips": ["Book the Sapa sleeper train ahead", "Carry cash for Ha Long and Sapa",
                 "Fly Hoi An to Saigon rather than two days by bus"],
    },
    "luxury_weekend": {
        "url": "https://worldinparis.com/romantic-weekend-in-paris-itinerary",
        "days": 3,
        "outline": ["Friday: rooftop drinks and a cabaret", "Saturday: the islands, a spa, a Seine cruise",
                    "Sunday: gardens and museums"],
        "must_sees": [r"louvre", r"sainte-chapelle", r"notre", r"seine", r"alexandre iii", r"moulin rouge|garnier",
                      r"m[eé]dicis|luxembourg|tuileries", r"vend[oô]me"],
        "rest_days": [],
        "tips": ["Book the Louvre ahead", "Book a riverside dinner table well ahead", "Book the cabaret early"],
    },
}
