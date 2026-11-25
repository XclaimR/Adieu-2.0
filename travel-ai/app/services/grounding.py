"""Keep only profile facts the user actually said.

Small local models fill in plausible guesses ("solo", "mid" budget, 7 days, a date) that the user never mentioned,
which would skip the questions that gather the trip's vibe. Each core field is kept only if the message contains
words that support it. Durations are also read directly from the text as a fallback. Wishes and mood are not
word-checked: the model reads those into notes, see personality_service.check_changes.
"""
import re

from app.schemas.trip_profile import Party, ProfileUpdates

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "a couple of": 2, "a few": 3,
}
_NUM = r"(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"

_PARTY_WORDS = {
    Party.SOLO: r"\b(solo|alone|myself|just me|on my own)\b",
    Party.COUPLE: r"\b(wife|husband|partner|girlfriend|boyfriend|fianc[eé]e?|couple|honeymoon|spouse|my love|my partner)\b",
    Party.FRIENDS: r"\b(friends?|buddies|mates|group|gang|crew)\b",
    Party.FAMILY: r"\b(family|kids?|children|child|mom|mum|dad|mother|father|parents|son|daughter|baby|toddler|"
                  r"sister|brother|siblings?|cousins?|aunt|uncle|grand(ma|pa|mother|father|parents)|niece|nephew|in-laws)\b",
}
# "with my sister", "my sister and I": a relative who is coming, when the model left the party empty.
_WITH_RELATIVE = (r"\b(with|and) (my|our) (sister|brother|siblings?|cousins?|mom|mum|dad|mother|father|parents|"
                  r"aunt|uncle|grand\w+|niece|nephew|in-laws)\b|\bmy (sister|brother|mom|mum|dad|cousin) and i\b")
_KIDS = r"\b(kids?|children|child|son|daughter|baby|toddler|family with kids)\b"
_NO_KIDS = r"\b(no kids|without (the )?kids|kid-?free|adults only)\b"
_BUDGET = r"\b(budget|cheap|tight|affordable|backpack\w*|save|saving|mid|moderate|mid-range|luxur\w*|splurge|fancy|treat|5-star|high-end|expensive|money)\b"
_DATE = (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|\b(spring|summer|autumn|fall|winter|"
         r"christmas|easter|diwali|new year|holiday|holidays|next|this)\b|\d")
_DURATION_CUE = rf"{_NUM}|\b(week|weekend|fortnight|days?|nights?)\b"

def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


def extract_duration(message: str) -> int | None:
    """Days mentioned in the message: "5 days", "a week", "4-5 days", "a weekend", "two weeks"."""
    text = message.lower()
    if m := re.search(rf"{_NUM}\s*(?:-|to)\s*{_NUM}\s*(days?|nights?)", text):
        return _to_int(m.group(2))
    if m := re.search(rf"{_NUM}\s*(days?|nights?)", text):
        return _to_int(m.group(1))
    if m := re.search(rf"{_NUM}\s*weeks?", text):
        return 7 * _to_int(m.group(1))
    # "a week", "a slow week", "a slow food week"; not "a trip next week", which is a date.
    if re.search(r"\b(?:a|one)\s+(?:(?!next|this|last)\w+\s+){0,2}week\b|\bweek-?long\b", text):
        return 7
    if re.search(r"\bfortnight\b", text):
        return 14
    if re.search(r"\bweekend\b", text):
        return 2
    return None


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token]


def ground_updates(updates: ProfileUpdates, message: str) -> ProfileUpdates:
    text = message.lower()
    u = updates.model_copy(deep=True)

    for place in ("destination", "origin", "return_to"):
        if (name := getattr(u, place)) and not any(
                w in text for w in re.split(r"[\s,]+", name.lower()) if len(w) >= 3):
            setattr(u, place, None)

    if u.party and not _has(_PARTY_WORDS[u.party], text):
        u.party = None
    if u.party is None and _has(_WITH_RELATIVE, text):
        u.party = Party.FAMILY
    # Keep a size only if the user said that number, or the party implies it ("my wife" = 2).
    # "Group of friends" says nothing about how many; the model likes to guess 4 or 6.
    said = {_to_int(m.group(1)) for m in re.finditer(rf"\b{_NUM}\b", text)}
    implied = {Party.COUPLE: 2, Party.SOLO: 1}.get(u.party)
    if u.party_size is not None and u.party_size not in said | {implied}:
        u.party_size = None
    if u.kids is True and not _has(_KIDS, text):
        u.kids = None
    if u.kids is False and not _has(_NO_KIDS, text):
        u.kids = None

    if u.budget_level and not _has(_BUDGET, text):
        u.budget_level = None
    if u.start_date and not _has(_DATE, text):
        u.start_date = None

    if u.duration_days is not None and not _has(_DURATION_CUE, text):
        u.duration_days = None
    if u.duration_days is None:
        u.duration_days = extract_duration(message)

    return u
