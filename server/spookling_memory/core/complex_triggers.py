"""Which questions are complex: a data table of trigger patterns (code, no model; Mohammed 13 Oct).

Each entry is (regex, category, example question). A question matching any entry is complex; nothing matching
means simple, the default. The `aggregate` category is also the aggregation-mode detector (more hits, its own
budget, window-first order, map-reduce); `preference` is also the preferences-on-demand detector. Patterns use
word boundaries ("Andrew" never matches "and", "Beforehand" never matches "before") and are case-insensitive
unless they say otherwise.
"""
from __future__ import annotations

import re

N = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|a few|a couple of|several)"
TRIGGERS: list[tuple[str, str, str]] = [
    # counting, summing, listing
    (r"\bhow many\b", "aggregate", "How many books did I read this year?"),
    (r"\bhow much\b", "aggregate", "How much did I spend on coffee?"),
    (r"\btotal(?:ly|led|ling|s)?\b", "aggregate", "What is the total I raised for charity?"),
    (r"\bin total\b", "aggregate", "How many hours did I train in total?"),
    (r"\baltogether\b", "aggregate", "How many countries have I visited altogether?"),
    (r"\bin all\b", "aggregate", "How many trips did I take in all?"),
    (r"\bcombined\b", "aggregate", "What is the combined cost of my two laptops?"),
    (r"\bsum(?: of| up|med up)?\b", "aggregate", "What's the sum of my gym fees?"),
    (r"\badd(?:s|ed|ing)? up\b", "aggregate", "Can you add up what I paid for the repairs?"),
    (r"\bcount(?:s|ed|ing)?\b", "aggregate", "Can you count the concerts I went to?"),
    (r"\bnumber of\b", "aggregate", "What's the number of plants I own?"),
    (r"\ball (?:the|of the|my)\b", "aggregate", "What are all the cities I lived in?"),
    (r"\bevery\b", "aggregate", "Which restaurants did I try every week?"),
    (r"\beach (?:time|one|of)\b", "aggregate", "What did I order each time I went there?"),
    (r"\bevery time\b", "aggregate", "What did I bring every time I hiked?"),
    # "list" as an ask, never as a noun ("my grocery list" is not a request to list; error-analysis-v2, d7c942c3)
    (r"^\s*(?:(?:can|could|would) you |please )?list\b|\blist (?:all|every|each|the|my|of)\b|\blisted\b", "aggregate",
     "List the courses I have taken."),
    (r"\bname (?:all|every|each)\b", "aggregate", "Name all the bands I saw live."),
    (r"\bwhich of\b", "aggregate", "Which of my friends came to the party?"),
    (r"\bwhich ones\b", "aggregate", "Which ones did I end up buying?"),
    (r"\bwhat are (?:all|the)\b", "aggregate", "What are the hobbies I picked up?"),
    (r"\bhow often\b", "aggregate", "How often do I go swimming?"),
    (r"\bso far\b", "aggregate", "How many miles have I run so far?"),
    (r"\btimes\b", "aggregate", "How many times did I visit my parents?"),
    (r"\bhow frequently\b", "aggregate", "How frequently did I play tennis?"),
    (r"\b(?:overall|cumulative)\b", "aggregate", "What is my overall spending on books?"),
    (r"\baverage\b", "aggregate", "What's the average price I paid per ticket?"),
    (r"\bper (?:week|month|day|year)\b", "aggregate", "How much do I save per month?"),
    # comparison and superlatives
    (r"\bmore than\b", "compare", "Did I spend more than last year on gifts?"),
    (r"\bless than\b", "compare", "Was my rent less than my friend's?"),
    (r"\bfewer than\b", "compare", "Did I read fewer than ten books?"),
    (r"\b(?:the )?most\b", "compare", "Which trip did I enjoy the most?"),
    (r"\b(?:the )?least\b", "compare", "Which subject did I like least?"),
    (r"\b(?:biggest|largest|smallest|highest|lowest|longest|shortest|oldest|newest|youngest|cheapest|priciest|fastest|slowest|best|worst|furthest|farthest|closest|heaviest|lightest)\b",
     "compare", "What was the biggest purchase I made?"),
    (r"\b(?:earliest|latest|most recent|first|last)\b", "compare", "When was the first time I went skiing?"),
    (r"\bfirst time\b", "compare", "When was the first time I tried sushi?"),
    (r"\blast time\b", "compare", "When was the last time I saw my cousin?"),
    (r"\bcompared? (?:to|with)\b", "compare", "How does my new job compare to my old one?"),
    (r"\bdifference between\b", "compare", "What's the difference between my two offers?"),
    (r"\b(?:faster|slower|longer|shorter|bigger|smaller|cheaper|higher|lower|older|younger|better|worse|earlier|later) than\b",
     "compare", "Was my 5K this year faster than last year?"),
    (r"\b(?:increase|decrease|improve|improvement|grew|dropped|went up|went down|changed)\b", "compare", "How much did my score improve?"),
    (r"\b(?:rank|ranking|order(?:ed)? by|top \d+|top (?:three|five|ten))\b", "compare", "What were my top three destinations?"),
    (r"\bthe same as\b", "compare", "Is my new phone the same as my old one?"),
    # two-part and conditional
    (r"\band\b", "conditional", "What did I buy and where did I buy it?"),
    (r"\bwhen I\b", "conditional", "Where was I living when I got the dog?"),
    (r"\bwhen (?:we|my|she|he|they)\b", "conditional", "What did I cook when my parents visited?"),
    (r"\bwhile\b", "conditional", "What was I reading while I was on holiday?"),
    (r"\bbefore\b", "conditional", "What car did I have before the Tesla?"),
    (r"\bafter\b", "conditional", "What did I do after the concert?"),
    (r"\bsince I\b", "conditional", "What have I changed since I moved?"),
    (r"\bthe time (?:that|when|I)\b", "conditional", "Who did I meet the time that I went to Paris?"),
    (r"\bduring the\b", "conditional", "What did I learn during the workshop?"),
    (r"\bat the same time\b", "conditional", "What else was I doing at the same time?"),
    (r"\bwhenever\b", "conditional", "What do I eat whenever I am sick?"),
    (r"\bif I\b", "conditional", "What would I need if I moved abroad?"),
    (r"\bunless\b", "conditional", "Do I go running unless it rains?"),
    (r"\bas soon as\b", "conditional", "What did I do as soon as I got home?"),
    (r"\bonce I\b", "conditional", "What did I plan once I finished the course?"),
    (r"\bor\b", "conditional", "Did I choose the red or the blue jacket?"),
    (r"\bbut\b", "conditional", "Which one did I like but not buy?"),
    # time spans and arithmetic
    (r"\bsince\b", "timespan", "How long have I lived here since moving?"),
    (r"\bbetween\b", "timespan", "What did I do between May and July?"),
    (r"\bduring\b", "timespan", "Which books did I read during the summer?"),
    (r"\bfrom\b.+?\bto\b", "timespan", "What did I study from 2019 to 2021?"),
    (r"\b(?:last|this|next|past|previous) (?:year|month|week|weekend|summer|winter|spring|autumn|fall|decade)\b", "timespan",
     "What did I buy last month?"),
    (r"\bthis year\b", "timespan", "How many races did I run this year?"),
    (rf"\b{N} (?:days?|weeks?|months?|years?) ago\b", "timespan", "What did I eat two weeks ago?"),
    (r"\bago\b", "timespan", "How long ago did I start learning Spanish?"),
    (r"\bhow long\b", "timespan", "How long have I been vegetarian?"),
    (r"\bhow many (?:days|weeks|months|years|hours|minutes)\b", "timespan", "How many days did the trip last?"),
    (r"\buntil\b", "timespan", "How long did I wait until the interview?"),
    (r"\bby the time\b", "timespan", "What had I finished by the time I left?"),
    (r"\bwhen did\b", "timespan", "When did I start my current job?"),
    (r"\bwhat (?:date|day|month|year|time)\b", "timespan", "What date did I move in?"),
    (r"\bin the (?:past|last) \w+\b", "timespan", "What did I buy in the past two weeks?"),
    (r"\b(?:recently|lately|nowadays|these days|currently|anymore|still)\b", "timespan", "What am I reading these days?"),
    (r"\b(?:january|february|march|april|june|july|august|september|october|november|december)\b", "timespan",
     "What did I do in March?"),
    (r"\b(?:19|20)\d\d\b", "timespan", "Where did I travel in 2022?"),
    (r"\bhow old\b", "timespan", "How old will I be at my friend's wedding?"),
    # back-references and coreference
    (r"\bthat (?:place|one|restaurant|shop|store|book|movie|show|trip|guy|woman|man|person|thing|game|song|course|city|hotel|café|cafe)\b",
     "coreference", "What was that place called again?"),
    (r"\bthe one (?:I|we|you|that|which)\b", "coreference", "What's the one I recommended to my sister?"),
    (r"^\s*(?:he|she|they|it|him|them)\b", "coreference", "She said what about the trip?"),
    (r"\b(?:her|his|their|its)\b", "coreference", "What is her favourite colour?"),
    (r"\bthe same\b", "coreference", "Did I order the same again?"),
    (r"\bagain\b", "coreference", "What was the name of the hotel again?"),
    (r"\bthe (?:previous|earlier|former|latter|other) (?:one|time)?\b", "coreference", "What was the previous one called?"),
    (r"\b(?:you|we) (?:mentioned|talked about|discussed|said|suggested|recommended)\b", "coreference",
     "What was the book you recommended?"),
    (r"\bremind me\b", "coreference", "Remind me what we decided about the venue?"),
    # multi-entity
    (r"\bboth\b", "multi_entity", "Did both my sisters come to the wedding?"),
    (r"\beither\b", "multi_entity", "Did either of my parents call?"),
    (r"\bneither\b", "multi_entity", "Did neither of my friends reply?"),
    (r"\ball of my\b", "multi_entity", "Did all of my cousins attend?"),
    (r"\bany of my\b", "multi_entity", "Have any of my plants died?"),
    (r"\bmy \w+(?: \w+)? and my\b", "multi_entity", "What do my brother and my sister do?"),
    (r"\b(?:my|our) (?:friends|parents|kids|children|siblings|cousins|colleagues|coworkers|pets|neighbors|neighbours)\b",
     "multi_entity", "Where did my friends go on holiday?"),
    # aggregation over categories
    (r"\bspen[dt] on\b", "category", "What have I spent on home improvements?"),
    (r"\brelated to\b", "category", "Which expenses were related to my car?"),
    (r"\babout my\b", "category", "What did I say about my health?"),
    (r"\binvolving\b", "category", "What events involving my team did I attend?"),
    (r"\b(?:kinds|types|sorts|categories) of\b", "category", "What kinds of music do I like?"),
    (r"\b(?:expenses?|costs?|purchases?|payments?|bills?)\b", "category", "What were my travel expenses?"),
    (r"\bin terms of\b", "category", "How am I doing in terms of savings?"),
    # recommendation and preference
    (r"\brecommend(?:s|ed|ation|ations)?\b", "preference", "Can you recommend a book for me?"),
    (r"\bsuggest(?:s|ed|ion|ions)?\b", "preference", "Suggest a restaurant for my anniversary."),
    (r"\bshould I\b", "preference", "What should I cook tonight?"),
    (r"\bwhat would I (?:like|enjoy|prefer|love)\b", "preference", "What would I like for my birthday?"),
    (r"\bbased on my\b", "preference", "Based on my taste, which film should I watch?"),
    (r"\bdo I (?:like|love|enjoy|prefer|hate|dislike)\b", "preference", "Do I like spicy food?"),
    (r"\bmy favou?rite\b", "preference", "What is my favourite cuisine?"),
    (r"\b(?:any )?(?:ideas|tips|advice) (?:for|on)\b", "preference", "Any ideas for a weekend trip?"),
    (r"\bwhat (?:to|should we) (?:do|watch|read|eat|cook|buy|wear|try)\b", "preference", "What to do this weekend?"),
    (r"\bwould (?:I|you) recommend\b", "preference", "Which laptop would you recommend for me?"),
    (r"\b(?:prefer|preference|preferences)\b", "preference", "What are my coffee preferences?"),
    (r"\bfor me\b", "preference", "Can you pick a hike for me?"),
]
COMPILED = [(re.compile(p, re.I), cat) for p, cat, _ in TRIGGERS]
CATEGORIES = sorted({cat for _, cat, _ in TRIGGERS})


RECALL_ASK = re.compile(r"\bremind me\b|\bwhat did i (?:say|tell you|mention|write|ask)\b", re.I)


def categories(question: str) -> set[str]:
    """The trigger categories a question matches; empty means simple. v4: a question asking to be reminded of
    something said ("remind me ... the average improvement") is never an aggregation, whatever words it holds."""
    cats = {cat for rx, cat in COMPILED if rx.search(question)}
    return cats - {"aggregate"} if RECALL_ASK.search(question) else cats


def is_complex(question: str) -> bool:
    return bool(categories(question))


def counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for _, cat, _ in TRIGGERS:
        out[cat] = out.get(cat, 0) + 1
    return out
