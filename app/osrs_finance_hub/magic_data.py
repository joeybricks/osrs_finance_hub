"""Spells that turn one item into money, and what each one needs.

None of this is in the prices API. It was taken from the wiki's spell pages
(High Level Alchemy, Low Level Alchemy, Lvl-1 to Lvl-7 Enchant and Enchant
Crossbow Bolt) and checked against /mapping in October 2026. Item ids are
stable; names are only here so the table can be read. Enchants whose input or
output cannot be traded on the GE are left out (plain jade bolts, for one).

Speeds are game ticks of 0.6 seconds between casts. Bolt spells enchant 10
bolts a cast and can be cast every tick, so for bolts the GE buy limit is
almost always the real limit, not casting time.
"""

from __future__ import annotations

from dataclasses import dataclass

TICK_SECONDS = 0.6

# Rune item ids by the name the spell pages use.
RUNE_IDS: dict[str, int] = {
    'Air': 556,
    'Blood': 565,
    'Cosmic': 564,
    'Death': 560,
    'Earth': 557,
    'Fire': 554,
    'Law': 563,
    'Mind': 558,
    'Nature': 561,
    'Soul': 566,
    'Water': 555,
}

# A staff, tome or combination staff can stand in for these, so they can be
# treated as free. Everything else has to be bought.
ELEMENTAL_RUNES = frozenset({"Air", "Water", "Earth", "Fire"})


@dataclass(frozen=True)
class Spell:
    key: str
    name: str
    level: int
    exp: float                       # per cast
    ticks: int                       # between casts
    members: bool
    runes: tuple[tuple[str, int], ...]
    per_cast: int = 1                # items converted by one cast


@dataclass(frozen=True)
class Enchant:
    spell: str                       # Spell.key
    input_id: int
    input_name: str
    output_id: int
    output_name: str

SPELLS: dict[str, Spell] = {
    'high_alch': Spell('high_alch', 'High Level Alchemy', level=55, exp=65.0, ticks=5,
        members=False, runes=(('Nature', 1), ('Fire', 5))),
    'low_alch': Spell('low_alch', 'Low Level Alchemy', level=21, exp=31.0, ticks=3,
        members=False, runes=(('Nature', 1), ('Fire', 3))),
    'enchant_1': Spell('enchant_1', 'Lvl-1 Enchant', level=7, exp=17.5, ticks=3,
        members=False, runes=(('Cosmic', 1), ('Water', 1))),
    'enchant_2': Spell('enchant_2', 'Lvl-2 Enchant', level=27, exp=37.0, ticks=3,
        members=False, runes=(('Air', 3), ('Cosmic', 1))),
    'enchant_3': Spell('enchant_3', 'Lvl-3 Enchant', level=49, exp=59.0, ticks=3,
        members=False, runes=(('Fire', 5), ('Cosmic', 1))),
    'enchant_4': Spell('enchant_4', 'Lvl-4 Enchant', level=57, exp=67.0, ticks=3,
        members=False, runes=(('Earth', 10), ('Cosmic', 1))),
    'enchant_5': Spell('enchant_5', 'Lvl-5 Enchant', level=68, exp=78.0, ticks=3,
        members=True, runes=(('Earth', 15), ('Water', 15), ('Cosmic', 1))),
    'enchant_6': Spell('enchant_6', 'Lvl-6 Enchant', level=87, exp=97.0, ticks=3,
        members=True, runes=(('Earth', 20), ('Fire', 20), ('Cosmic', 1))),
    'enchant_7': Spell('enchant_7', 'Lvl-7 Enchant', level=93, exp=110.0, ticks=3,
        members=True, runes=(('Blood', 20), ('Soul', 20), ('Cosmic', 1))),
    'bolts_opal': Spell('bolts_opal', 'Enchant Crossbow Bolt (Opal)', level=4, exp=9.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Air', 2)), per_cast=10),
    'bolts_sapphire': Spell('bolts_sapphire', 'Enchant Crossbow Bolt (Sapphire)', level=7, exp=17.5, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Water', 1), ('Mind', 1)), per_cast=10),
    'bolts_jade': Spell('bolts_jade', 'Enchant Crossbow Bolt (Jade)', level=14, exp=19.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Earth', 2)), per_cast=10),
    'bolts_pearl': Spell('bolts_pearl', 'Enchant Crossbow Bolt (Pearl)', level=24, exp=29.5, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Water', 2)), per_cast=10),
    'bolts_emerald': Spell('bolts_emerald', 'Enchant Crossbow Bolt (Emerald)', level=27, exp=37.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Air', 3), ('Nature', 1)), per_cast=10),
    'bolts_red_topaz': Spell('bolts_red_topaz', 'Enchant Crossbow Bolt (Red Topaz)', level=29, exp=33.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Fire', 2)), per_cast=10),
    'bolts_ruby': Spell('bolts_ruby', 'Enchant Crossbow Bolt (Ruby)', level=49, exp=59.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Fire', 5), ('Blood', 1)), per_cast=10),
    'bolts_diamond': Spell('bolts_diamond', 'Enchant Crossbow Bolt (Diamond)', level=57, exp=67.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Earth', 10), ('Law', 2)), per_cast=10),
    'bolts_dragonstone': Spell('bolts_dragonstone', 'Enchant Crossbow Bolt (Dragonstone)', level=68, exp=78.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Earth', 15), ('Soul', 1)), per_cast=10),
    'bolts_onyx': Spell('bolts_onyx', 'Enchant Crossbow Bolt (Onyx)', level=87, exp=97.0, ticks=1,
        members=True, runes=(('Cosmic', 1), ('Fire', 20), ('Death', 1)), per_cast=10),
}

JEWELLERY_ENCHANTS: tuple[Enchant, ...] = (
    Enchant('enchant_1', 1637, 'Sapphire ring', 2550, 'Ring of recoil'),
    Enchant('enchant_1', 11072, 'Sapphire bracelet', 11074, 'Bracelet of clay'),
    Enchant('enchant_1', 1656, 'Sapphire necklace', 3853, 'Games necklace(8)'),
    Enchant('enchant_1', 1694, 'Sapphire amulet', 1727, 'Amulet of magic'),
    Enchant('enchant_1', 21081, 'Opal ring', 21126, 'Ring of pursuit'),
    Enchant('enchant_1', 21117, 'Opal bracelet', 21177, 'Expeditious bracelet'),
    Enchant('enchant_1', 21090, 'Opal necklace', 21143, 'Dodgy necklace'),
    Enchant('enchant_1', 21108, 'Opal amulet', 21160, 'Amulet of bounty'),
    Enchant('enchant_2', 1639, 'Emerald ring', 2552, 'Ring of dueling(8)'),
    Enchant('enchant_2', 1658, 'Emerald necklace', 5521, 'Binding necklace'),
    Enchant('enchant_2', 11076, 'Emerald bracelet', 11079, 'Castle wars bracelet(3)'),
    Enchant('enchant_2', 1696, 'Emerald amulet', 1729, 'Amulet of defence'),
    Enchant('enchant_2', 21084, 'Jade ring', 21129, 'Ring of returning(5)'),
    Enchant('enchant_2', 21120, 'Jade bracelet', 21180, 'Flamtaer bracelet'),
    Enchant('enchant_2', 21093, 'Jade necklace', 21146, 'Necklace of passage(5)'),
    Enchant('enchant_2', 21111, 'Jade amulet', 21163, 'Amulet of chemistry'),
    Enchant('enchant_3', 1641, 'Ruby ring', 2568, 'Ring of forging'),
    Enchant('enchant_3', 11085, 'Ruby bracelet', 11088, 'Inoculation bracelet'),
    Enchant('enchant_3', 1698, 'Ruby amulet', 1725, 'Amulet of strength'),
    Enchant('enchant_3', 21087, 'Topaz ring', 21140, "Efaritay's aid"),
    Enchant('enchant_3', 21123, 'Topaz bracelet', 21183, 'Bracelet of slaughter'),
    Enchant('enchant_3', 21096, 'Topaz necklace', 21157, 'Necklace of faith'),
    Enchant('enchant_3', 21114, 'Topaz amulet', 21166, 'Burning amulet(5)'),
    Enchant('enchant_4', 1643, 'Diamond ring', 2570, 'Ring of life'),
    Enchant('enchant_4', 1662, 'Diamond necklace', 11090, 'Phoenix necklace'),
    Enchant('enchant_4', 11092, 'Diamond bracelet', 11095, 'Abyssal bracelet(5)'),
    Enchant('enchant_4', 1700, 'Diamond amulet', 1731, 'Amulet of power'),
    Enchant('enchant_5', 1645, 'Dragonstone ring', 2572, 'Ring of wealth'),
    Enchant('enchant_5', 1664, 'Dragon necklace', 11113, 'Skills necklace'),
    Enchant('enchant_5', 11115, 'Dragonstone bracelet', 11126, 'Combat bracelet'),
    Enchant('enchant_5', 1702, 'Dragonstone amulet', 1704, 'Amulet of glory'),
    Enchant('enchant_6', 6575, 'Onyx ring', 6583, 'Ring of stone'),
    Enchant('enchant_6', 6577, 'Onyx necklace', 11128, 'Berserker necklace'),
    Enchant('enchant_6', 11130, 'Onyx bracelet', 11133, 'Regen bracelet'),
    Enchant('enchant_6', 6581, 'Onyx amulet', 6585, 'Amulet of fury'),
    Enchant('enchant_7', 19538, 'Zenyte ring', 19550, 'Ring of suffering'),
    Enchant('enchant_7', 19535, 'Zenyte necklace', 19547, 'Necklace of anguish'),
    Enchant('enchant_7', 19532, 'Zenyte bracelet', 19544, 'Tormented bracelet'),
    Enchant('enchant_7', 19541, 'Zenyte amulet', 19553, 'Amulet of torture'),
)

BOLT_ENCHANTS: tuple[Enchant, ...] = (
    Enchant('bolts_opal', 879, 'Opal bolts', 9236, 'Opal bolts (e)'),
    Enchant('bolts_opal', 21955, 'Opal dragon bolts', 21932, 'Opal dragon bolts (e)'),
    Enchant('bolts_sapphire', 9337, 'Sapphire bolts', 9240, 'Sapphire bolts (e)'),
    Enchant('bolts_sapphire', 21963, 'Sapphire dragon bolts', 21940, 'Sapphire dragon bolts (e)'),
    Enchant('bolts_jade', 21957, 'Jade dragon bolts', 21934, 'Jade dragon bolts (e)'),
    Enchant('bolts_pearl', 880, 'Pearl bolts', 9238, 'Pearl bolts (e)'),
    Enchant('bolts_pearl', 21959, 'Pearl dragon bolts', 21936, 'Pearl dragon bolts (e)'),
    Enchant('bolts_emerald', 9338, 'Emerald bolts', 9241, 'Emerald bolts (e)'),
    Enchant('bolts_emerald', 21965, 'Emerald dragon bolts', 21942, 'Emerald dragon bolts (e)'),
    Enchant('bolts_red_topaz', 9336, 'Topaz bolts', 9239, 'Topaz bolts (e)'),
    Enchant('bolts_red_topaz', 21961, 'Topaz dragon bolts', 21938, 'Topaz dragon bolts (e)'),
    Enchant('bolts_ruby', 9339, 'Ruby bolts', 9242, 'Ruby bolts (e)'),
    Enchant('bolts_ruby', 21967, 'Ruby dragon bolts', 21944, 'Ruby dragon bolts (e)'),
    Enchant('bolts_diamond', 9340, 'Diamond bolts', 9243, 'Diamond bolts (e)'),
    Enchant('bolts_diamond', 21969, 'Diamond dragon bolts', 21946, 'Diamond dragon bolts (e)'),
    Enchant('bolts_diamond', 34690, 'Diamond ascension bolts', 34696, 'Diamond ascension bolts (e)'),
    Enchant('bolts_dragonstone', 9341, 'Dragonstone bolts', 9244, 'Dragonstone bolts (e)'),
    Enchant('bolts_dragonstone', 21971, 'Dragonstone dragon bolts', 21948, 'Dragonstone dragon bolts (e)'),
    Enchant('bolts_onyx', 9342, 'Onyx bolts', 9245, 'Onyx bolts (e)'),
    Enchant('bolts_onyx', 21973, 'Onyx dragon bolts', 21950, 'Onyx dragon bolts (e)'),
    Enchant('bolts_onyx', 34702, 'Onyx ascension bolts', 34708, 'Onyx ascension bolts (e)'),
)
