#!/usr/bin/env python3
"""
build_tts_deck.py

Turns a CSV of card data (Wildward-style) + a folder of per-card face images
+ group-specific back images into:
  - sprite-sheet PNGs (grids of card faces, TTS "CustomDeck" format), one
    set of sheets per BACK GROUP so each sheet can have a single uniform
    BackURL
  - a TTS save-game JSON (SavedObject) that spawns one deck object per
    sheet, with each card's Nickname/Description filled in from the CSV
  - optionally, 5 self-refilling "draft bags" (Monster + one per faction)
    so players can pull unlimited copies of any card to build their
    40-card Expedition decks before a game

-----------------------------------------------------------------------------
BACK GROUPS (Wildward-specific, edit get_back_group() to change)
-----------------------------------------------------------------------------
  Monster      -> all "Monster - ..." types                (1 back)
  Expedition   -> Retainer / Event / Tool / Disposable Tool / Biome (1 back)
  Hero-H / Hero-T / Hero-R / Hero-C -> heroes of each faction (own back each)
  Hero-None    -> heroes with no fixed faction (e.g. Allessio) (own back,
                  optional -- only 1 card in the base set)

-----------------------------------------------------------------------------
REQUIRED FILE NAMING CONVENTION -- FACE IMAGES
-----------------------------------------------------------------------------
Face images are matched primarily BY CARD NAME (since MSE exports name the
file after the card, not the number), e.g. "Meat Feast.png" for a card
named "Meat Feast". Matching is whitespace/case-insensitive and strips
characters that aren't allowed in filenames (\ / : * ? " < > |), so minor
differences in how MSE sanitized the name won't break the match.

If a name match isn't found, the script falls back to looking for a file
named after the card's number (the part before the slash in the CSV's
"Number" column, e.g. "47.png" for "47/288") for backwards compatibility.

All face images go in one folder (--images-dir).

-----------------------------------------------------------------------------
BACK IMAGES
-----------------------------------------------------------------------------
Put them in one folder (--backs-dir), named exactly after the back-group
key, e.g.:
    Monster.png
    Expedition.png
    Hero-H.png
    Hero-T.png
    Hero-R.png
    Hero-C.png
    Hero-None.png      (optional; only needed for factionless heroes)

-----------------------------------------------------------------------------
HOSTING
-----------------------------------------------------------------------------
TTS loads images by URL, not local path.
  1. Upload every file in the output folder somewhere public (GitHub raw,
     Imgur, Dropbox "?dl=1" link, etc.)
  2. Re-run with --face-url-base / --back-url-base pointing at that
     location, OR open the generated .json afterwards and find/replace the
     placeholder URLs (marked "REPLACE_ME_...").
  3. Drop the final .json into your TTS "Saved Objects" folder:
       Documents/My Games/Tabletop Simulator/Saves/Saved Objects/
     Then in TTS: Objects menu -> Saved Objects -> pick it.

-----------------------------------------------------------------------------
HOW TO RUN
-----------------------------------------------------------------------------
No command-line flags needed. Edit the CONFIG block right below the imports
(paths, URL bases, whether to build draft bags) then just run:

    python3 build_tts_deck.py
"""

import csv
import json
import math
import re
import sys
import uuid
from pathlib import Path

from PIL import Image

# =============================================================================
# CONFIG -- edit these, then just run: python3 build_tts_deck.py
# =============================================================================

CSV_PATH = "out.csv"              # path to the card data CSV
IMAGES_DIR = "./../../Images"       # folder of face images (named by card Name, or number)
BACKS_DIR = "./cardbacks"        # folder of back images (Monster.png, Expedition.png, etc.)
OUTPUT_DIR = "./tts_output"       # where sheet PNGs + the save JSON get written

# Leave these blank ("") to get placeholder URLs you fill in by hand later,
# or set them once you know where the output images will be hosted.
FACE_URL_BASE = ""                # e.g. "https://raw.githubusercontent.com/you/repo/main/faces"
BACK_URL_BASE = ""                # e.g. "https://raw.githubusercontent.com/you/repo/main/backs"

# Build the 5 self-refilling draft bags (Monster + Faction-H/T/R/C) for
# pre-game deckbuilding? True/False.
DRAFT_BAGS = True

# Build a "Deck Builder" Notecard: paste a decklist into its Notes
# (right-click > Notes), click its button, and it spawns + auto-merges
# the deck. Works for ANY card in the whole CSV, not just Expedition.
DECK_BUILDER = True

# =============================================================================

GRID_COLS = 10
GRID_ROWS = 7
PER_SHEET = GRID_COLS * GRID_ROWS  # 70, the classic safe TTS limit
CARD_W = 744
CARD_H = 1039

PLACEHOLDER_FACE_URL = "REPLACE_ME_FACE_SHEET_URL"
PLACEHOLDER_BACK_URL = "REPLACE_ME_BACK_URL"

EXPEDITION_TYPES = {"Retainer", "Event", "Tool", "Disposable Tool", "Biome"}
FACTIONS = ("H", "T", "R", "C")

ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|,\']')


def parse_number(num_field):
    m = re.match(r"\s*(\d+)\s*/\s*\d+\s*", num_field or "")
    return int(m.group(1)) if m else None


def normalize_name(s):
    """Normalize a card name / filename stem so MSE-exported filenames
    reliably match CSV names despite minor sanitization differences."""
    s = (s or "").strip()
    s = ILLEGAL_FILENAME_CHARS.sub("", s)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def get_back_group(row):
    """Wildward-specific grouping. Edit this to match your own game's rules."""
    t = row.get("Type", "") or ""
    if t.startswith("Monster"):
        return "Monster"
    if t.startswith("Hero"):
        faction = (row.get("Faction") or "").strip()
        if faction in ("H", "T", "R", "C"):
            return f"Hero-{faction}"
        return "Hero-None"
    return "Expedition"  # Retainer, Event, Tool, Disposable Tool, Biome


def is_expedition(row):
    return (row.get("Type", "") or "") in EXPEDITION_TYPES


def load_csv(csv_path):
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for row in rows:
        row["_num"] = parse_number(row.get("Number", ""))
        row["_back_group"] = get_back_group(row)
    missing = [r for r in rows if r["_num"] is None]
    if missing:
        print(f"WARNING: {len(missing)} rows had no parseable Number field, "
              f"skipped: {[r.get('Name') for r in missing]}", file=sys.stderr)
    rows = [r for r in rows if r["_num"] is not None]
    rows.sort(key=lambda r: r["_num"])
    return rows


def build_image_index(images_dir):
    """Maps normalized filename stem -> path, for every image file in the
    folder. Used to match MSE's name-based exports."""
    index = {}
    for p in images_dir.iterdir():
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            index[normalize_name(p.stem)] = p
    return index


def find_image_by_stem(folder, stem):
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def resolve_face_image(row, images_dir, image_index):
    """Try matching by card name first (MSE export convention), then fall
    back to matching by card number (older convention)."""
    key = normalize_name(row.get("Name", ""))
    if key in image_index:
        return image_index[key]
    else:
        print(f"WARNING: No file named {key}")
    return find_image_by_stem(images_dir, row["_num"])


def build_face_sheets(rows, images_dir, image_index, output_dir, group_key):
    """Build 10x7 face sheets for one back-group's rows."""
    sheets = []
    width = CARD_W
    height = CARD_H
    if group_key=="Monster":
        width = CARD_H
        height = CARD_W
    n_sheets = math.ceil(len(rows) / PER_SHEET)
    for sheet_idx in range(n_sheets):
        chunk = rows[sheet_idx * PER_SHEET:(sheet_idx + 1) * PER_SHEET]
        sheet_img = Image.new("RGBA", (width * GRID_COLS, height * GRID_ROWS), (0, 0, 0, 0))
        for i, row in enumerate(chunk):
            col, grow = i % GRID_COLS, i // GRID_COLS
            img_path = resolve_face_image(row, images_dir, image_index)
            if img_path is None:
                print(f"WARNING: no face image for '{img_path}' "
                      f"(#{row['_num']}), leaving blank cell", file=sys.stderr)
            else:
                try:
                    card_img = Image.open(img_path).convert("RGBA").resize((width, height))
                    sheet_img.paste(card_img, (col * width, grow * height), card_img)
                except Exception as e:
                    print(f"WARNING: failed to load {img_path}: {e}", file=sys.stderr)
        safe_group = group_key.replace(" ", "_")
        out_path = output_dir / f"sheet_faces_{safe_group}_{sheet_idx + 1}.png"
        sheet_img.save(out_path)
        sheets.append({"path": out_path, "rows": chunk, "group": group_key, "sheet_num": sheet_idx + 1})
        print(f"Wrote {out_path} ({len(chunk)} cards, group={group_key})")
    return sheets


def card_description(row):
    desc_parts = []
    if row.get("Wincon"):
        desc_parts.append(f"Win Condition: {row['Wincon']}")
    if row.get("Basic Text") and row["Basic Text"] not in ("-", ""):
        desc_parts.append(row["Basic Text"])
    for stat in ("Health", "Suspicion", "Mystery", "Stability", "Stamina", "Cost", "Tool Activation"):
        if row.get(stat):
            desc_parts.append(f"{stat}: {row[stat]}")
    return "\n".join(desc_parts)


def counter_kind(row):
    """Which counter-button script (if any) a card should get, based on
    its type. Only Monster (4 stats) and Retainer (1 Stamina) have real
    trackable per-card values in this game -- other Expedition types don't."""
    if row.get("_back_group") == "Monster":
        return "Monster"
    if (row.get("Type", "") or "") == "Retainer":
        return "Retainer"
    return None


# Both scripts parse their OWN stat values out of the card's Description
# field at click time (card_description() already writes lines like
# "Health: 7"), so the same static script text works for every card of a
# given kind -- no per-card script generation needed.

MONSTER_COUNTER_SCRIPT = """local COUNTERS_ADDED = false

function onLoad()
    self.createButton({
        click_function = "addCounters",
        function_owner = self,
        label = "+Counters",
        position = {0, 0.15, -1.35},
        width = 900,
        height = 300,
        font_size = 180,
        color = {0.2, 0.4, 0.7},
        font_color = {1, 1, 1},
    })
end

function addCounters(obj, color, alt_click)
    if COUNTERS_ADDED then
        broadcastToColor("Counters already added.", color, {1, 1, 0.3})
        return
    end
    local desc = self.getDescription() or ""
    local stats = {
        {name = "Health",    dx = -0.65, dz =  1.0},
        {name = "Suspicion", dx =  0.65, dz =  1.0},
        {name = "Mystery",   dx = -0.65, dz = -1.0},
        {name = "Stability", dx =  0.65, dz = -1.0},
    }
    local basePos = self.getPosition()
    local baseRot = self.getRotation()
    local spawnedAny = false
    for _, s in ipairs(stats) do
        local value = desc:match(s.name .. ":%s*(%-?%d+)")
        if value then
            local pos = {x = basePos.x + s.dx, y = basePos.y + 0.3, z = basePos.z + s.dz}
            local counter = spawnObject({
                type = "Counter",
                position = pos,
                rotation = baseRot,
                scale = {0.6, 0.6, 0.6},
            })
            counter.setName(s.name)
            local v = tonumber(value)
            Wait.frames(function()
                counter.Counter.setValue(v)
            end, 2)
            spawnedAny = true
        end
    end
    if spawnedAny then
        COUNTERS_ADDED = true
        broadcastToColor("Counters added.", color, {0.3, 1, 0.3})
    else
        broadcastToColor("Could not find stat values on this card.", color, {1, 0.3, 0.3})
    end
end
"""

RETAINER_COUNTER_SCRIPT = """local COUNTERS_ADDED = false

function onLoad()
    self.createButton({
        click_function = "addCounters",
        function_owner = self,
        label = "+Counter",
        position = {0, 0.15, -1.35},
        width = 900,
        height = 300,
        font_size = 180,
        color = {0.2, 0.4, 0.7},
        font_color = {1, 1, 1},
    })
end

function addCounters(obj, color, alt_click)
    if COUNTERS_ADDED then
        broadcastToColor("Counter already added.", color, {1, 1, 0.3})
        return
    end
    local desc = self.getDescription() or ""
    local value = desc:match("Stamina:%s*(%-?%d+)")
    if value then
        local basePos = self.getPosition()
        local baseRot = self.getRotation()
        local pos = {x = basePos.x, y = basePos.y + 0.3, z = basePos.z - 1.0}
        local counter = spawnObject({
            type = "Counter",
            position = pos,
            rotation = baseRot,
            scale = {0.6, 0.6, 0.6},
        })
        counter.setName("Stamina")
        local v = tonumber(value)
        Wait.frames(function()
            counter.Counter.setValue(v)
        end, 2)
        COUNTERS_ADDED = true
        broadcastToColor("Counter added.", color, {0.3, 1, 0.3})
    else
        broadcastToColor("Could not find a Stamina value on this card.", color, {1, 0.3, 0.3})
    end
end
"""


def lua_str(s):
    """Escape a Python string into a Lua double-quoted string literal."""
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
    return f'"{s}"'


DECK_BUILDER_SCRIPT_TEMPLATE = """-- Auto-generated by build_tts_deck.py. Paste a decklist into this object's
-- Notes (right-click > Notes), one card per line, formatted:
--     3 Meat Feast
--     2x Clever Management
--     Careless Researcher        (no count = 1 copy)
-- Then click the "Build Deck" button. Matching is case-insensitive.

local DECKS = {
%(decks)s
}

local CARD_DB = {
%(card_db)s
}

-- Shared counter-button scripts (same text used on cards built via the
-- master library / draft bags) -- see counter_kind() in build_tts_deck.py.
local MONSTER_COUNTER_SCRIPT = [[
%(monster_script)s
]]

local RETAINER_COUNTER_SCRIPT = [[
%(retainer_script)s
]]

function onLoad()
    self.createButton({
        click_function = "buildDeck",
        function_owner = self,
        label = "Build Deck",
        position = {0, 0.3, 1.05},
        rotation = {0, 0, 0},
        width = 1600,
        height = 500,
        font_size = 250,
        color = {0.2, 0.6, 0.2},
        font_color = {1, 1, 1},
    })
end

function buildDeck(obj, color, alt_click)
    local text = self.getDescription() or ""
    local spawned = {}
    local notFound = {}

    for line in string.gmatch(text, "[^\\r\\n]+") do
        local count, name = line:match("^%%s*(%%d+)%%s*[xX]?%%s*(.+)$")
        if not count then
            count, name = "1", line
        end
        count = tonumber(count) or 1
        if name then
            name = name:gsub("^%%s+", ""):gsub("%%s+$", "")
        end
        if name and name ~= "" then
            local entry = CARD_DB[string.lower(name)]
            if entry then
                for i = 1, count do
                    local deckData = DECKS[entry.Deck]
                    local script = ""
                    if entry.Kind == "Monster" then
                        script = MONSTER_COUNTER_SCRIPT
                    elseif entry.Kind == "Retainer" then
                        script = RETAINER_COUNTER_SCRIPT
                    end
                    local data = {
                        Name = "Card",
                        Nickname = entry.Nickname,
                        Description = entry.Description,
                        CardID = entry.CardID,
                        CustomDeck = {[tostring(entry.Deck)] = deckData},
                        LuaScript = script,
                    }
                    local basePos = self.getPosition()
                    local pos = {
                        basePos.x + (math.random(-50, 50) * 0.01),
                        basePos.y + 2 + (#spawned * 0.15),
                        basePos.z + 3,
                    }
                    local newObj = spawnObjectData({data = data, position = pos, rotation = {0, 180, 180}})
                    table.insert(spawned, newObj)
                end
            else
                table.insert(notFound, name)
            end
        end
    end

    if #notFound > 0 then
        broadcastToColor("Card(s) not found: " .. table.concat(notFound, ", "), color, {1, 0.3, 0.3})
    end

    if #spawned > 1 then
        Wait.frames(function()
            group(spawned)
            broadcastToColor("Built deck with " .. #spawned .. " cards.", color, {0.3, 1, 0.3})
        end, 30)
    elseif #spawned == 1 then
        broadcastToColor("Only 1 valid card found, spawned as a single card.", color, {1, 1, 0.3})
    else
        broadcastToColor("No valid cards found in the Notes text.", color, {1, 0.3, 0.3})
    end
end
"""


def build_deck_builder_object(card_lookup, position, example_names):
    """One 'Notecard' object: paste a decklist into its Notes (Description
    field, standard on every TTS object -- no custom UI needed for input),
    click its Build Deck button, and it spawns + auto-merges the requested
    cards into a deck. Reuses the master library's images via CARD_DB, so
    duplicating a card in the list is free (same as decklists-dir / draft
    bags), and it works for ANY back-group, not just Expedition."""
    decks = {}       # deck_key(int) -> CustomDeck entry
    card_db_lines = []
    for lookup in card_lookup.values():
        deck_key_str = next(iter(lookup["CustomDeck"].keys()))
        deck_key = int(deck_key_str)
        entry = lookup["CustomDeck"][deck_key_str]
        decks[deck_key] = entry

        row = lookup["row"]
        name_key = normalize_name(row.get("Name", ""))
        kind = counter_kind(row)
        kind_field = f'Kind={lua_str(kind)}, ' if kind else ""
        card_db_lines.append(
            f'  [{lua_str(name_key)}] = {{CardID={lookup["CardID"]}, Deck={deck_key}, {kind_field}'
            f'Nickname={lua_str(row.get("Name", ""))}, Description={lua_str(card_description(row))}}},'
        )

    deck_lines = []
    for key in sorted(decks):
        e = decks[key]
        deck_lines.append(
            f'  [{key}] = {{FaceURL={lua_str(e["FaceURL"])}, BackURL={lua_str(e["BackURL"])}, '
            f'NumWidth={e["NumWidth"]}, NumHeight={e["NumHeight"]}, '
            f'BackIsHidden={"true" if e["BackIsHidden"] else "false"}, '
            f'UniqueBack={"true" if e["UniqueBack"] else "false"}, Type=0}},'
        )

    script = DECK_BUILDER_SCRIPT_TEMPLATE % {
        "decks": "\n".join(deck_lines),
        "card_db": "\n".join(card_db_lines),
        "monster_script": MONSTER_COUNTER_SCRIPT,
        "retainer_script": RETAINER_COUNTER_SCRIPT,
    }

    example_text = "\n".join(f"1 {n}" for n in example_names[:3])
    description = (
        "Paste your decklist here, one card per line: '3 Meat Feast' or "
        "'2x Clever Management' or just 'Careless Researcher' for 1 copy. "
        "Then click Build Deck.\n\nExample:\n" + example_text
    )

    obj = {
        "Name": "Notecard",
        "Transform": {
            "posX": position[0], "posY": 1, "posZ": position[1],
            "rotX": 0, "rotY": 180, "rotZ": 0,
            "scaleX": 1.5, "scaleY": 1.5, "scaleZ": 1.5,
        },
        "Nickname": "Deck Builder",
        "Description": description,
        "GUID": uuid.uuid4().hex[:6],
        "LuaScript": script,
        "LuaScriptState": "",
    }
    print(f"Built deck-builder Notecard with {len(card_db_lines)} cards in its database")
    return obj


def build_json(all_sheets, backs_dir, output_dir, face_url_base, back_url_base):
    """Builds the master library deck objects (one copy of every card).
    Returns (object_states, card_lookup, x_offset) where card_lookup maps
    card number -> {"CardID", "CustomDeck": {key: entry}, "row": row} so
    draft bags / the Deck Builder can reuse the exact same image/back
    reference (needed to duplicate a card's image across multiple copies)."""
    object_states = []
    card_lookup = {}
    x_offset = 0
    deck_counter = 0
    missing_backs = set()

    for sheet in all_sheets:
        deck_counter += 1
        group_key = sheet["group"]
        face_filename = sheet["path"].name
        face_url = (face_url_base.rstrip("/") + "/" + face_filename) if face_url_base else PLACEHOLDER_FACE_URL

        back_path = find_image_by_stem(backs_dir, group_key) if backs_dir else None
        if back_path is not None:
            back_url = (back_url_base.rstrip("/") + "/" + back_path.name) if back_url_base else PLACEHOLDER_BACK_URL
        else:
            back_url = PLACEHOLDER_BACK_URL
            missing_backs.add(group_key)

        custom_deck_key = str(deck_counter)
        custom_deck_entry = {
            "FaceURL": face_url,
            "BackURL": back_url,
            "NumWidth": GRID_COLS,
            "NumHeight": GRID_ROWS,
            "BackIsHidden": True,
            "UniqueBack": False,
            "Type": 0,
        }

        contained = []
        deck_ids = []
        for i, row in enumerate(sheet["rows"]):
            card_id = deck_counter * 100 + i
            deck_ids.append(card_id)
            description = card_description(row)
            kind = counter_kind(row)
            script = {"Monster": MONSTER_COUNTER_SCRIPT, "Retainer": RETAINER_COUNTER_SCRIPT}.get(kind, "")

            contained.append({
                "Name": "Card",
                "Nickname": row.get("Name", f"Card {row['_num']}"),
                "Description": description,
                "CardID": card_id,
                "GUID": uuid.uuid4().hex[:6],
                "CustomDeck": {custom_deck_key: custom_deck_entry},
                "LuaScript": script,
                "LuaScriptState": "",
                "Transform": {
                    "posX": 0, "posY": 0, "posZ": 0,
                    "rotX": 0, "rotY": 180, "rotZ": 180,
                    "scaleX": 1, "scaleY": 1, "scaleZ": 1,
                },
            })

            card_lookup[row["_num"]] = {
                "CardID": card_id,
                "CustomDeck": {custom_deck_key: custom_deck_entry},
                "row": row,
            }

        deck_object = {
            "Name": "DeckCustom",
            "Transform": {
                "posX": x_offset, "posY": 1, "posZ": 0,
                "rotX": 0, "rotY": 180, "rotZ": 180,
                "scaleX": 1, "scaleY": 1, "scaleZ": 1,
            },
            "Nickname": f"Wildward - {group_key} ({sheet['sheet_num']})",
            "CustomDeck": {custom_deck_key: custom_deck_entry},
            "DeckIDs": deck_ids,
            "ContainedObjects": contained,
        }
        object_states.append(deck_object)
        x_offset += 4

    if missing_backs:
        print(f"WARNING: no back image found for group(s): {sorted(missing_backs)}. "
              f"Those decks got a placeholder BackURL -- fix manually or add the "
              f"missing file(s) to --backs-dir and re-run.", file=sys.stderr)

    return object_states, card_lookup, x_offset


REFILL_SCRIPT = """function onObjectLeaveContainer(container, object)
    if container == self then
        self.putObject(object.clone({
            ['position'] = {self.getPosition().x, self.getPosition().y + 2, self.getPosition().z}
        }))
    end
end"""


def _refill_card_object(row, lookup):
    kind = counter_kind(row)
    script = {"Monster": MONSTER_COUNTER_SCRIPT, "Retainer": RETAINER_COUNTER_SCRIPT}.get(kind, "")
    return {
        "Name": "Card",
        "Nickname": row.get("Name", f"Card {row['_num']}"),
        "Description": card_description(row),
        "CardID": lookup["CardID"],
        "GUID": uuid.uuid4().hex[:6],
        "CustomDeck": lookup["CustomDeck"],
        "LuaScript": script,
        "LuaScriptState": "",
        "Transform": {
            "posX": 0, "posY": 0, "posZ": 0,
            "rotX": 0, "rotY": 180, "rotZ": 180,
            "scaleX": 1, "scaleY": 1, "scaleZ": 1,
        },
    }


def build_single_infinite_bag(rows, card_lookup, bag_label, position):
    """One bag holding all unique cards passed in (each a different card),
    with a self-refill script: whenever a card is taken out, a clone of it
    is instantly put back in the bag. A Bag can hold many DIFFERENT objects
    at once without them merging into a deck, so one bag per group is all
    that's needed -- no per-card bags. Players browse/take cards via the
    bag's right-click 'Search' menu (shows thumbnails) or by opening it."""
    contained = []
    skipped = 0
    for row in rows:
        lookup = card_lookup.get(row["_num"])
        if lookup is None:
            skipped += 1
            continue
        contained.append(_refill_card_object(row, lookup))

    if skipped:
        print(f"WARNING: {skipped} cards in '{bag_label}' had no master "
              f"library entry, skipped when building the infinite bag", file=sys.stderr)

    bag_obj = {
        "Name": "Bag",
        "Transform": {
            "posX": position[0], "posY": 1, "posZ": position[1],
            "rotX": 0, "rotY": 180, "rotZ": 0,
            "scaleX": 1.5, "scaleY": 1.5, "scaleZ": 1.5,
        },
        "Nickname": f"{bag_label} - Infinite Card Box",
        "Description": "Pull any card, unlimited copies. Right-click > Search to browse.",
        "GUID": uuid.uuid4().hex[:6],
        "LuaScript": REFILL_SCRIPT,
        "LuaScriptState": "",
        "ContainedObjects": contained,
    }
    print(f"Built infinite bag '{bag_label}' holding {len(contained)} unique cards")
    return bag_obj


def build_draft_bags(rows, card_lookup):
    """The 5 bags used for pre-game deckbuilding:
      - Monster: every monster card (unfiltered, no faction restriction)
      - Faction-H/T/R/C: every Expedition-type card (Retainer/Event/Tool/
        Disposable Tool/Biome) whose Faction field CONTAINS that letter
        (Faction is a combo string like "HTRC" or "CH" -- a card can
        legally belong to more than one faction bag)
    Returns list of Bag objects, laid out side by side."""
    monster_rows = [r for r in rows if r["_back_group"] == "Monster"]
    bag_defs = [("Monster", monster_rows)]
    for fac in FACTIONS:
        fac_rows = [r for r in rows if is_expedition(r) and fac in (r.get("Faction") or "")]
        bag_defs.append((f"Faction-{fac}", fac_rows))

    bags = []
    x = 0
    for label, group_rows in bag_defs:
        bags.append(build_single_infinite_bag(group_rows, card_lookup, label, (x, -6)))
        x += 3
    return bags


def main():
    images_dir = Path(IMAGES_DIR)
    backs_dir = Path(BACKS_DIR)
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_csv(Path(CSV_PATH))
    print(f"Loaded {len(rows)} cards from CSV")

    image_index = build_image_index(images_dir)
    print(f"Indexed {len(image_index)} face images in {images_dir}")

    groups = {}
    for row in rows:
        groups.setdefault(row["_back_group"], []).append(row)

    print("Back groups found:")
    for g, r in sorted(groups.items()):
        print(f"  {g}: {len(r)} cards")

    all_sheets = []
    for group_key, group_rows in groups.items():
        all_sheets.extend(build_face_sheets(group_rows, images_dir, image_index, output_dir, group_key))

    object_states, card_lookup, x_offset = build_json(
        all_sheets, backs_dir, output_dir, FACE_URL_BASE, BACK_URL_BASE)

    if DRAFT_BAGS:
        object_states.extend(build_draft_bags(rows, card_lookup))

    if DECK_BUILDER:
        example_names = [r.get("Name", "") for r in rows[:3]]
        object_states.append(build_deck_builder_object(card_lookup, (10, -6), example_names))

    save = {"SaveName": "Wildward Full Deck", "GameMode": "Wildward", "ObjectStates": object_states}
    out_path = output_dir / "wildward_tts_save.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save, f, indent=2)
    print(f"Wrote {out_path}")

    print("\nDone. Next steps:")
    print("1. Host all sheet_faces_*.png and your back images somewhere public.")
    print("2. If FACE_URL_BASE/BACK_URL_BASE were blank, open the JSON and")
    print("   replace REPLACE_ME_FACE_SHEET_URL / REPLACE_ME_BACK_URL with real URLs.")
    print("3. Copy the JSON into your TTS 'Saved Objects' folder and load it from")
    print("   Objects > Saved Objects in-game.")


if __name__ == "__main__":
    main()
