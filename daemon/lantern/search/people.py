"""People words in queries, Polish and English: "zdjęcie kobiety", "z ludźmi", "bez ludzi", "grupa", "selfie",
"dwie osoby", "photos of men". They filter the photos section by what lantern/people.py counted; documents that
contain the word are still found by it (the words stay in the query for the other lists).
"""

from dataclasses import dataclass

from lantern.places import name_key

WOMEN = {"kobieta", "kobiety", "kobiet", "kobiecie", "kobiete", "kobietami", "dziewczyna", "dziewczyny", "dziewczyn",
         "dziewczynie", "dziewczyne", "dziewczynka", "dziewczynki", "dziewczynek", "woman", "women", "girl", "girls",
         "lady", "ladies"}  # fmt: skip
MEN = {"mezczyzna", "mezczyzny", "mezczyzn", "mezczyzni", "mezczyznie", "mezczyzne", "mezczyznami", "chlopak",
       "chlopaka", "chlopaki", "chlopakiem", "chlopcy", "chlopiec", "chlopca", "chlopcow", "facet", "faceta", "faceci",
       "facetem", "man", "men", "boy", "boys", "guy", "guys", "gentleman"}  # fmt: skip
PEOPLE = {"ludzie", "ludzi", "ludzmi", "osoba", "osoby", "osob", "osobami", "osobe", "czlowiek", "czlowieka",
          "czlowiekiem", "people", "person", "persons"}  # fmt: skip
GROUP = {"grupa", "grupy", "grupie", "grupe", "grupowe", "grupowa", "grupowy", "grupowo", "tlum", "tlumy", "tlumu",
         "group", "groups", "crowd", "crowds"}  # fmt: skip
PORTRAIT = {"selfie", "selfiki", "selfik", "portret", "portrety", "portretu", "portrait", "portraits", "headshot"}
WITHOUT = {"bez", "without", "no"}
NUMBERS = {"dwie": 2, "dwoch": 2, "dwa": 2, "two": 2, "2": 2, "trzy": 3, "trzech": 3, "three": 3, "3": 3,
           "cztery": 4, "czterech": 4, "four": 4, "4": 4}  # fmt: skip
GROUP_MIN = 3  # Vision misses people behind others: "a group" from three (4+ was seen as 4+ in 22 of 40, COCO)
PORTRAIT_FACE = 0.03  # the face covers at least 3% of the photo
# words that only join the others ("kobieta i mężczyzna", "photos of women with ...")
GLUE = {"i", "oraz", "z", "ze", "and", "with", "of", "a", "an", "the", "na", "w", "we", "in"}


@dataclass(frozen=True)
class PeopleFilter:
    sql: str  # condition on files (alias f)
    params: tuple
    words: frozenset[int]  # indices of the query's words that said it (with "bez", numbers)


def parse(query: str) -> PeopleFilter | None:
    keys = [name_key(w) for w in query.split()]
    conditions: list[str] = []
    params: list = []
    used: set[int] = set()
    i = 0
    while i < len(keys):
        k = keys[i]
        nxt = keys[i + 1] if i + 1 < len(keys) else ""
        if k in WITHOUT and (nxt in PEOPLE or nxt in WOMEN or nxt in MEN):  # "bez ludzi", "without people"
            conditions.append("f.people = 0")
            used |= {i, i + 1}
            i += 2
            continue
        if k in NUMBERS and nxt in PEOPLE | WOMEN | MEN:  # "dwie osoby", "two women"
            column = "women" if nxt in WOMEN else "men" if nxt in MEN else "people"
            conditions.append(f"f.{column} {'=' if column == 'people' else '>='} ?")
            params.append(NUMBERS[k])
            used |= {i, i + 1}
            i += 2
            continue
        if k in WOMEN:
            conditions.append("f.women >= 1")
        elif k in MEN:
            conditions.append("f.men >= 1")
        elif k in PEOPLE:
            conditions.append("f.people >= 1")
        elif k in GROUP:
            conditions.append(f"f.people >= {GROUP_MIN}")
        elif k in PORTRAIT:
            conditions.append(f"f.people = 1 AND f.face_area >= {PORTRAIT_FACE}")
        else:
            i += 1
            continue
        used.add(i)
        i += 1
    if not conditions:
        return None
    unique = list(dict.fromkeys(conditions))  # "kobieta i kobieta" once
    return PeopleFilter(" AND ".join(f"({c})" for c in unique), tuple(params), frozenset(used))


def only_people_words(query: str, people: PeopleFilter, also: set[str] = frozenset()) -> bool:
    """Nothing but people words, words joining them and `also` (e.g. "zdjęcia"): browse the photos ("kobieta i
    mężczyzna", "selfie", "zdjęcia kobiet")."""
    keys = [name_key(w) for w in query.split()]
    return all(i in people.words or k in GLUE or k in also or not k for i, k in enumerate(keys))
