"""The time zone of the record, for the stamps the plugin writes and the stamps it reads.

Python 3 standard library only, and nothing from this plugin: record_format.py and recortex.py both import it. A record
states its time convention once, in words ("US Pacific time, written as 2026-03-04 14:10 PST"): the `timezone` entry of
the project file, which setup.py writes and which the hub repeats. This module reads that statement, and:

  stamp, iso   write a moment in that zone, with the abbreviation of the moment (PST in winter, PDT in summer) and the
               offset the marker needs, or in UTC when the record states no zone this table knows;
  stamp_utc    read "2026-03-04 14:10 PST" as the moment it names, or None when the zone is not one this table knows
               (the caller then falls back to the time GitHub gave the comment).

Python 3.8 has no zoneinfo, so the table is here: the abbreviations a record writes, their offsets, and the daylight
saving rules of the United States (since 2007), the European Union and the United Kingdom, east Australia and New
Zealand. An abbreviation that names more than one zone (IST, and CST outside the United States) is not guessed beyond
the United States reading of CST; a zone without an entry reads as unknown, which is the safe side. The abbreviations
are the ones the linter's stamp form accepts: two to five capital letters, or UTC with an offset.
"""

import datetime
import re

# standard abbreviation, daylight abbreviation, standard offset in minutes east of UTC, rule of the family
PAIRS = (("PST", "PDT", -480, "US"), ("MST", "MDT", -420, "US"), ("CST", "CDT", -360, "US"), ("EST", "EDT", -300, "US"),
         ("AKST", "AKDT", -540, "US"), ("GMT", "BST", 0, "EU"), ("WET", "WEST", 0, "EU"), ("CET", "CEST", 60, "EU"),
         ("EET", "EEST", 120, "EU"), ("AEST", "AEDT", 600, "AU"), ("NZST", "NZDT", 720, "NZ"))
# abbreviations that never change with the season, in minutes east of UTC; GMT is here, so a record that states GMT keeps GMT
FIXED = {"UTC": 0, "UT": 0, "GMT": 0, "HST": -600, "JST": 540, "KST": 540, "SGT": 480, "HKT": 480, "SAST": 120, "EAT": 180,
         "WAT": 60, "MSK": 180, "PKT": 300, "WIB": 420}
# what a statement in words, with no stamp in it, names; the first phrase found counts (more specific ones first)
WORDS = (("central european", "CET"), ("eastern european", "EET"), ("western european", "WET"), ("british", "BST"),
         ("alaska", "AKST"), ("hawaii", "HST"), ("pacific", "PST"), ("mountain", "MST"), ("central", "CST"),
         ("eastern", "EST"), ("japan", "JST"), ("universal", "UTC"), ("utc", "UTC"), ("gmt", "GMT"))

OFFSET = re.compile(r"UTC([+-])([0-9]{1,2})(?::([0-9]{2}))?")
EXAMPLE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} ([A-Z]{2,5}|UTC[+-][0-9]{1,2}(?::[0-9]{2})?)(?![A-Za-z0-9+:-])")
STAMP = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}) ([A-Z]{2,5}|UTC[+-][0-9]{1,2}(?::[0-9]{2})?)(?![A-Za-z0-9+:-])")

_BY_NAME = {}
for _std, _dst, _minutes, _rule in PAIRS:
    _BY_NAME[_std] = (_std, _dst, _minutes, _rule)
    _BY_NAME[_dst] = (_std, _dst, _minutes, _rule)


def sunday(year, month, which):
    """The date of the `which`-th Sunday of a month (1 is the first), or the last one when `which` is -1."""
    if which > 0:
        first = datetime.date(year, month, 1)
        return first + datetime.timedelta(days=(6 - first.weekday()) % 7 + 7 * (which - 1))
    following = datetime.date(year + (month == 12), month % 12 + 1, 1)
    last = following - datetime.timedelta(days=1)
    return last - datetime.timedelta(days=(last.weekday() + 1) % 7)


def at(year, month, which, hour, minutes):
    """The moment, in UTC, of local `hour` o'clock on that Sunday in a zone `minutes` east of UTC."""
    day = sunday(year, month, which)
    return datetime.datetime(day.year, day.month, day.day, hour, 0, tzinfo=datetime.timezone.utc) - datetime.timedelta(
        minutes=minutes)


def daylight(rule, moment, standard):
    """True when daylight saving time is in force at `moment` (aware) in a zone of `rule` whose standard offset is
    `standard` minutes. United States: second Sunday of March 02:00 to first Sunday of November 02:00 local. European
    Union and United Kingdom: last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC. East Australia: first
    Sunday of October to first Sunday of April, at 02:00 standard time. New Zealand: last Sunday of September to first
    Sunday of April, at 02:00 standard time."""
    moment = moment.astimezone(datetime.timezone.utc)
    year = moment.year
    if rule == "US":
        return at(year, 3, 2, 2, standard) <= moment < at(year, 11, 1, 2, standard + 60)
    if rule == "EU":
        return at(year, 3, -1, 1, 0) <= moment < at(year, 10, -1, 1, 0)
    if rule == "AU":
        return moment < at(year, 4, 1, 2, standard) or moment >= at(year, 10, 1, 2, standard)
    if rule == "NZ":
        return moment < at(year, 4, 1, 2, standard) or moment >= at(year, 9, -1, 2, standard)
    return False


def sign(minutes):
    return "-" if minutes < 0 else "+"


class Zone:
    """A zone the record states: one label and offset all the year, or a standard and a daylight abbreviation that follow
    the rule of their family."""

    def __init__(self, label, minutes, pair=None):
        self.label, self.minutes, self.pair = label, minutes, pair

    def at(self, moment):
        """(abbreviation, minutes east of UTC) in force at `moment`."""
        if self.pair is None:
            return self.label, self.minutes
        standard, summer, minutes, rule = self.pair
        if daylight(rule, moment, minutes):
            return summer, minutes + 60
        return standard, minutes

    def local(self, moment):
        label, minutes = self.at(moment)
        return moment.astimezone(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(minutes=minutes), label, minutes

    def stamp(self, moment):
        """'2026-03-04 14:10 PST': the date, the minute and the abbreviation of the moment in this zone."""
        local, label, _ = self.local(moment)
        return "%s %s" % (local.strftime("%Y-%m-%d %H:%M"), label)

    def iso(self, moment):
        """'2026-03-04T14:10-08:00': the same moment, as a marker's time with its offset (Z for an offset of zero)."""
        local, label, minutes = self.local(moment)
        if minutes == 0:
            return local.strftime("%Y-%m-%dT%H:%MZ")
        return "%s%s%02d:%02d" % (local.strftime("%Y-%m-%dT%H:%M"), sign(minutes), abs(minutes) // 60, abs(minutes) % 60)


UTC = Zone("UTC", 0)


def offset_of(label):
    """Minutes east of UTC that an abbreviation names (BST is one hour, PDT seven hours west), or None when it is not one
    this table knows."""
    if label in FIXED:
        return FIXED[label]
    found = OFFSET.fullmatch(label)
    if found:
        minutes = int(found.group(2)) * 60 + int(found.group(3) or 0)
        return None if minutes > 14 * 60 else -minutes if found.group(1) == "-" else minutes
    if label in _BY_NAME:
        std, dst, minutes, rule = _BY_NAME[label]
        return minutes + 60 if label == dst else minutes
    return None


def named(label):
    """The Zone an abbreviation names, or None: UTC and the fixed ones, UTC with an offset, and the daylight pairs (a
    zone that is named by one of its two abbreviations follows the season)."""
    if label in FIXED or OFFSET.fullmatch(label):
        minutes = offset_of(label)
        return None if minutes is None else Zone(label, minutes)
    if label in _BY_NAME:
        return Zone(label, _BY_NAME[label][2], _BY_NAME[label])
    return None


def zone_of(text):
    """The Zone a statement of the record's time convention names, or None. The statement is the project file's `timezone`
    ("US Pacific time, written as 2026-03-04 14:10 PST"): the abbreviation of the example stamp counts first, then UTC with
    an offset, then a bare abbreviation ("CET"), then the words (Pacific, Central European, ...). None when it names nothing
    this table knows."""
    if not isinstance(text, str):
        return None
    found = EXAMPLE.search(text)
    if found:
        return named(found.group(1))
    found = OFFSET.search(text)
    if found:
        return named(found.group(0))
    for token in re.findall(r"(?<![A-Za-z0-9])[A-Z]{2,5}(?![A-Za-z0-9])", text):
        if named(token) is not None:
            return named(token)
    lowered = text.lower()
    for phrase, label in WORDS:
        if phrase in lowered:
            return named(label)
    return None


def stamp_utc(stamp):
    """The moment (aware, UTC) that '2026-03-04 14:10 PST' names, from the abbreviation's own offset, or None when the
    text is not such a stamp or its zone is one this table does not know."""
    found = STAMP.match(stamp or "")
    if not found:
        return None
    minutes = offset_of(found.group(6))
    if minutes is None:
        return None
    try:
        local = datetime.datetime(int(found.group(1)), int(found.group(2)), int(found.group(3)), int(found.group(4)),
                                  int(found.group(5)))
    except ValueError:
        return None
    return (local - datetime.timedelta(minutes=minutes)).replace(tzinfo=datetime.timezone.utc)
