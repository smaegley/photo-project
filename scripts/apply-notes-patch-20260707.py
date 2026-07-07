#!/usr/bin/env python3
"""
Patch script: apply notes/caption corrections from 2026-07-07 session.

What this fixes:
  - 440 photo notes fields:
      * 269 bare Ditto/Continuation entries resolved to
        Card: Ditto (Original card: '...') format
      * ~180 missing notes filled in for mags 10-17 from card image re-reads
      * 3 manual corrections (mag 11 slide 22, mag 12 slide 33, mag 14 slide 31)
  - 3 captions corrected (mag 11 slide 22/23, mag 12 slide 33)

Safe to run multiple times -- skips rows already matching the target value.

Usage (on LXC 209):
  cd /opt/photo-project
  python3 scripts/apply-notes-patch-20260707.py
  # dry-run first:
  python3 scripts/apply-notes-patch-20260707.py --dry-run
"""
import sqlite3, json, sys

DRY_RUN = "--dry-run" in sys.argv
DB_PATH = "data/photos.db"

CAPTION_UPDATES = [
    [
        371,
        "U of Minn. Anti-War Protesters On Campus"
    ],
    [
        372,
        "U of Minn. Anti-War Protesters On Campus"
    ],
    [
        418,
        "Karen & Goose"
    ]
]

NOTES_UPDATES = [
    [
        9,
        "Card: Ditto (Original card: 'Rock City, Tenn')"
    ],
    [
        15,
        "Card: Ditto (Original card: 'Christmas 1963')"
    ],
    [
        21,
        "Card: Ditto (Original card: 'Evergreen, Colo 6/64')"
    ],
    [
        26,
        "Card: Ditto (Original card: 'June 1964')"
    ],
    [
        30,
        "Card: Ditto (Original card: 'Colo Springs 6/64')"
    ],
    [
        36,
        "Card: Ditto (Original card: 'Foothills 6/64')"
    ],
    [
        38,
        "Card: Ditto (Original card: 'Gold Mines')"
    ],
    [
        40,
        "Card: Ditto (Original card: 'Trout Hatchery')"
    ],
    [
        44,
        "Card: Ditto (Original card: '1964')"
    ],
    [
        45,
        "Card: Ditto (Original card: '1964')"
    ],
    [
        46,
        "Card: Ditto (Original card: '1964')"
    ],
    [
        47,
        "Card: Ditto (Original card: '1964')"
    ],
    [
        48,
        "Card: Ditto (Original card: '1964')"
    ],
    [
        52,
        "Card: Ditto (Original card: 'On Lake')"
    ],
    [
        58,
        "Card: Ditto (Original card: 'Grand Lake, Colo')"
    ],
    [
        59,
        "Card: Ditto (Original card: 'Grand Lake, Colo')"
    ],
    [
        64,
        "Card: Ditto (Original card: 'July 4, 1964')"
    ],
    [
        71,
        "Card: Ditto (Original card: 'S. Platte River, July 64')"
    ],
    [
        72,
        "Card: Ditto (Original card: 'S. Platte River, July 64')"
    ],
    [
        74,
        "Card: Ditto (Original card: 'On Way To Mt Evans')"
    ],
    [
        77,
        "Card: Ditto (Original card: 'Sail Boating with the Bowmans on Cherry Creek Reservoir')"
    ],
    [
        86,
        "Card: Ditto (Original card: 'Smallmouth Bass Caught @ Wood's Res Aug 1964')"
    ],
    [
        95,
        "Card: Ditto (Original card: 'Royal Gorge Sept 1964')"
    ],
    [
        97,
        "Card: Ditto (Original card: 'Royal Gorge Sept 1964')"
    ],
    [
        98,
        "Card: Ditto (Original card: 'Royal Gorge Sept 1964')"
    ],
    [
        99,
        "Card: Ditto (Original card: 'Royal Gorge Sept 1964')"
    ],
    [
        105,
        "Card: Ditto (Original card: 'S. Platte R. Canyon - Oct 64')"
    ],
    [
        120,
        "Card: Ditto (Original card: 'City Park')"
    ],
    [
        129,
        "Card: Ditto (Original card: 'The Becks')"
    ],
    [
        133,
        "Card: Ditto (Original card: 'Jan 1965')"
    ],
    [
        157,
        "Card: Ditto (Original card: 'Cherry Creek on the Water & in the Air')"
    ],
    [
        191,
        "Card: Ditto (Original card: 'Karen - One Week')"
    ],
    [
        192,
        "Card: Ditto (Original card: 'Karen - One Week')"
    ],
    [
        193,
        "Card: Ditto (Original card: 'Karen - One Week')"
    ],
    [
        195,
        "Card: Ditto (Original card: '2 Weeks')"
    ],
    [
        218,
        "Card: Ditto (Original card: 'Sept 1965')"
    ],
    [
        225,
        "Card: Ditto (Original card: 'Yellowstone Nat. Park')"
    ],
    [
        234,
        "Card: Ditto (Original card: 'Old Faithful')"
    ],
    [
        235,
        "Card: Ditto (Original card: 'Old Faithful')"
    ],
    [
        248,
        "Card: Ditto (Original card: 'Dick Roz. & Bus Dowty')"
    ],
    [
        259,
        "Card: Ditto (Original card: 'Karen @ 4 mos')"
    ],
    [
        260,
        "Card: Ditto (Original card: 'Karen @ 4 mos')"
    ],
    [
        284,
        "Card: Ditto (Original card: 'House, Mar 1966')"
    ],
    [
        285,
        "Card: Ditto (Original card: 'House, Mar 1966')"
    ],
    [
        312,
        "Card: Ditto (Original card: 'Sept 1966')"
    ],
    [
        313,
        "Card: Ditto (Original card: 'Sept 1966')"
    ],
    [
        316,
        "Card: Ditto (Original card: 'Riding, Jan 1967')"
    ],
    [
        317,
        "Card: 'Winter in Minnesota Jan - Mar 1967'"
    ],
    [
        318,
        "Card: Ditto (Original card: 'Winter in Minnesota Jan - Mar 1967')"
    ],
    [
        319,
        "Card: Ditto (Original card: 'Winter in Minnesota Jan - Mar 1967')"
    ],
    [
        320,
        "Card: Ditto (Original card: 'Winter in Minnesota Jan - Mar 1967')"
    ],
    [
        321,
        "Card: Ditto (Original card: 'Winter in Minnesota Jan - Mar 1967')"
    ],
    [
        322,
        "Card: Ditto (Original card: 'Winter in Minnesota Jan - Mar 1967')"
    ],
    [
        323,
        "Card: Ditto (Original card: 'Winter in Minnesota Jan - Mar 1967')"
    ],
    [
        324,
        "Card: 'Scott Hulbert's Birthday Party, Mar 1967'"
    ],
    [
        325,
        "Card: Ditto (Original card: 'Scott Hulbert's Birthday Party, Mar 1967')"
    ],
    [
        326,
        "Card: Ditto (Original card: 'Scott Hulbert's Birthday Party, Mar 1967')"
    ],
    [
        328,
        "Card: 'Como Park Observatory'"
    ],
    [
        329,
        "Card: 'Karen on New Tricycle'"
    ],
    [
        330,
        "Card: Ditto (Original card: 'Karen on New Tricycle')"
    ],
    [
        331,
        "Card: 'Karen on Ag Campus with Cattle'"
    ],
    [
        332,
        "Card: Ditto (Original card: 'Karen on Ag Campus with Cattle')"
    ],
    [
        333,
        "Card: Ditto (Original card: 'Karen on Ag Campus with Cattle')"
    ],
    [
        334,
        "Card: 'Northwest DC-8 (?) Making Touch & Go at Anoka Co. Airport'"
    ],
    [
        335,
        "Card: Ditto (Original card: 'Northwest DC-8 (?) Making Touch & Go at Anoka Co. Airport')"
    ],
    [
        336,
        "Card: Ditto (Original card: 'Northwest DC-8 (?) Making Touch & Go at Anoka Co. Airport')"
    ],
    [
        337,
        "Card: Ditto (Original card: 'Northwest DC-8 (?) Making Touch & Go at Anoka Co. Airport')"
    ],
    [
        338,
        "Card: 'Karen in Yard June 1967'"
    ],
    [
        339,
        "Card: Ditto (Original card: 'Karen in Yard June 1967')"
    ],
    [
        340,
        "Card: Ditto (Original card: 'Karen in Yard June 1967')"
    ],
    [
        341,
        "Card: Ditto (Original card: 'Karen in Yard June 1967')"
    ],
    [
        342,
        "Card: Ditto (Original card: 'Karen in Yard June 1967')"
    ],
    [
        346,
        "Card: 'Duluth Minn Sept 1967'"
    ],
    [
        347,
        "Card: 'Duluth, Harbor - Lake Superior - From Holiday Inn'"
    ],
    [
        348,
        "Card: Ditto (Original card: 'Duluth, Harbor - Lake Superior - From Holiday Inn')"
    ],
    [
        349,
        "Card: Ditto (Original card: 'Duluth, Harbor - Lake Superior - From Holiday Inn')"
    ],
    [
        350,
        "Card: 'State Fair Nite Shots of Midway'"
    ],
    [
        351,
        "Card: Ditto (Original card: 'State Fair Nite Shots of Midway')"
    ],
    [
        352,
        "Card: Ditto (Original card: 'State Fair Nite Shots of Midway')"
    ],
    [
        353,
        "Card: Ditto (Original card: 'State Fair Nite Shots of Midway')"
    ],
    [
        354,
        "Card: 'Day Shots of 1967 Fair'"
    ],
    [
        355,
        "Card: Ditto (Original card: 'Day Shots of 1967 Fair')"
    ],
    [
        356,
        "Card: 'Gondola Lift'"
    ],
    [
        357,
        "Card: 'Ferris Wheel'"
    ],
    [
        358,
        "Card: 'Space Needle'"
    ],
    [
        359,
        "Card: 'Mounted Police'"
    ],
    [
        360,
        "Card: 'Fire Works'"
    ],
    [
        361,
        "Card: Ditto (Original card: 'Fire Works')"
    ],
    [
        362,
        "Card: Ditto (Original card: 'Fire Works')"
    ],
    [
        363,
        "Card: 'Karen & Krisie Oct 1967'"
    ],
    [
        364,
        "Card: Ditto (Original card: 'Karen & Krisie Oct 1967')"
    ],
    [
        365,
        "Card: Ditto (Original card: 'Karen & Krisie Oct 1967')"
    ],
    [
        366,
        "Card: 'Campus - Northrup Aud.'"
    ],
    [
        367,
        "Card: 'Miss. River, Eastbank'"
    ],
    [
        368,
        "Card: 'More Miss. R.'"
    ],
    [
        369,
        "Card: 'Wash Br., Westbank'"
    ],
    [
        370,
        "Card: 'The Mall'"
    ],
    [
        372,
        "Card: 'Anti-War Protesters on Campus'"
    ],
    [
        373,
        "Card: 'Halloween 1967'"
    ],
    [
        374,
        "Card: Ditto (Original card: 'Halloween 1967')"
    ],
    [
        375,
        "Card: 'Karen with Santa Claus Christmas 1967'"
    ],
    [
        376,
        "Card: Ditto (Original card: 'Karen with Santa Claus Christmas 1967')"
    ],
    [
        377,
        "Card: Ditto (Original card: 'Karen with Santa Claus Christmas 1967')"
    ],
    [
        378,
        "Card: Ditto (Original card: 'Karen with Santa Claus Christmas 1967')"
    ],
    [
        379,
        "Card: Ditto (Original card: 'Karen with Santa Claus Christmas 1967')"
    ],
    [
        384,
        "Card: 'Aircraft @ Wold-Chamberlain'"
    ],
    [
        385,
        "Card: Ditto (Original card: 'Aircraft @ Wold-Chamberlain')"
    ],
    [
        386,
        "Card: 'SEALS @'"
    ],
    [
        387,
        "Card: 'COMO ZOO'"
    ],
    [
        388,
        "Card: 'JENNY'S 3RD'"
    ],
    [
        389,
        "Card: 'BIRTHDAY PARTY'"
    ],
    [
        390,
        "Card: 'KAREN SWIMMING'"
    ],
    [
        391,
        "Card: 'JUNE 1968'"
    ],
    [
        392,
        "Card: Ditto (Original card: 'JUNE 1968')"
    ],
    [
        393,
        "Card: 'MINNEHAHA FALLS'"
    ],
    [
        394,
        "Card: 'JUNE 1968'"
    ],
    [
        395,
        "Card: 'KRISSY'S 3RD'"
    ],
    [
        396,
        "Card: 'BIRTHDAY'"
    ],
    [
        397,
        "Card: Ditto (Original card: 'BIRTHDAY')"
    ],
    [
        398,
        "Card: Ditto (Original card: 'BIRTHDAY')"
    ],
    [
        399,
        "Card: Ditto (Original card: 'BIRTHDAY')"
    ],
    [
        401,
        "Card: 'KAREN @ LAKE NOKOMIS'"
    ],
    [
        402,
        "Card: 'ST. PAUL CATHEDRAL'"
    ],
    [
        403,
        "Card: 'A LAKE NEAR BRAINERD'"
    ],
    [
        404,
        "Card: 'AT THE'"
    ],
    [
        405,
        "Card: 'HOLIDAY INN'"
    ],
    [
        406,
        "Card blank"
    ],
    [
        407,
        "Card: 'POOL WITH'"
    ],
    [
        408,
        "Card blank"
    ],
    [
        409,
        "Card: 'THE BECKS'"
    ],
    [
        410,
        "Card blank"
    ],
    [
        411,
        "Card: 'AUG 1968'"
    ],
    [
        412,
        "Card blank"
    ],
    [
        413,
        "Card blank"
    ],
    [
        414,
        "Card blank"
    ],
    [
        415,
        "Card: 'BECKS &'"
    ],
    [
        416,
        "Card: 'MAEGLEYS''"
    ],
    [
        417,
        "Card: '@ U. OF MINN.'"
    ],
    [
        418,
        "Karen and Goose"
    ],
    [
        419,
        "Card: 'KAREN, PAM &'"
    ],
    [
        420,
        "Card: 'MELONIE IN'"
    ],
    [
        421,
        "Card: 'SWIMMING POOL'"
    ],
    [
        422,
        "Card: 'LAKE'"
    ],
    [
        423,
        "Card: 'MILLE LACS'"
    ],
    [
        424,
        "Card: 'ON WAY TO CANADA'"
    ],
    [
        425,
        "Card: 'CANADIAN SUNSET'"
    ],
    [
        426,
        "Card: 'MINAKI LODGE'"
    ],
    [
        427,
        "Card: 'LAKE of the WOODS'"
    ],
    [
        428,
        "Card: 'AUG. 1968'"
    ],
    [
        429,
        "Card: Ditto (Original card: 'AUG. 1968')"
    ],
    [
        430,
        "Card: Ditto (Original card: 'AUG. 1968')"
    ],
    [
        431,
        "Card: 'KENORA, CANADA'"
    ],
    [
        432,
        "Card: 'LAKE of WOODS'"
    ],
    [
        433,
        "Card: Ditto (Original card: 'LAKE of WOODS')"
    ],
    [
        434,
        "Card: Ditto (Original card: 'LAKE of WOODS')"
    ],
    [
        435,
        "Card: 'ST. CROIX R. Tay. Falls'"
    ],
    [
        436,
        "Card: 'LAKE PHALEN (M\\'s Fish!)'"
    ],
    [
        437,
        "Card: 'WITH'"
    ],
    [
        438,
        "Card: 'KAREN'"
    ],
    [
        439,
        "Card: Ditto (Original card: 'KAREN')"
    ],
    [
        440,
        "Card: Ditto (Original card: 'KAREN')"
    ],
    [
        441,
        "Card: 'Two Harbors, Minn'"
    ],
    [
        442,
        "Card: 'SEPT 1968'"
    ],
    [
        443,
        "Card: 'LAKE SUPERIOR'"
    ],
    [
        444,
        "Card: 'NORTH SHORE'"
    ],
    [
        445,
        "Card: 'GOOSEBERRY FALLS'"
    ],
    [
        446,
        "Card: 'NORTH SHORE'"
    ],
    [
        447,
        "Card: Ditto (Original card: 'NORTH SHORE')"
    ],
    [
        450,
        "Card: 'GRAND MARAIS'"
    ],
    [
        451,
        "Card: 'ENTRANCE TO DULUTH'"
    ],
    [
        452,
        "Card: 'HARBOR ~ BRIDGE'"
    ],
    [
        453,
        "Card: Ditto (Original card: 'HARBOR ~ BRIDGE')"
    ],
    [
        454,
        "Card: Ditto (Original card: 'HARBOR ~ BRIDGE')"
    ],
    [
        455,
        "Card: 'KAREN'"
    ],
    [
        456,
        "Card: 'OCT. 1968'"
    ],
    [
        458,
        "Card: 'KAREN -'"
    ],
    [
        459,
        "Card: 'SUMMER'"
    ],
    [
        460,
        "Card: '1968'"
    ],
    [
        461,
        "Card: 'WINTER 1968'"
    ],
    [
        462,
        "Card: 'FRONT of APT.'"
    ],
    [
        463,
        "Card: '1239 FIFIELD PL.'"
    ],
    [
        464,
        "Card: 'KAREN on'"
    ],
    [
        465,
        "Card: 'Snow'"
    ],
    [
        466,
        "Card: 'SLIDE'"
    ],
    [
        467,
        "Card: 'BACK of APT BLDG.'"
    ],
    [
        468,
        "Card: 'STEVIE x3-4 mos'"
    ],
    [
        469,
        "Card: 'with KAREN Reading'"
    ],
    [
        470,
        "Card: 'a Story'"
    ],
    [
        471,
        "Card: 'More of'"
    ],
    [
        472,
        "Card: 'Stevie'"
    ],
    [
        473,
        "Card: Ditto (Original card: 'Stevie')"
    ],
    [
        474,
        "Card: 'STEVIE ~5mos'"
    ],
    [
        475,
        "Card: 'APR. 1969'"
    ],
    [
        476,
        "Card: 'with Karen'"
    ],
    [
        477,
        "Card: 'outside APR 1969'"
    ],
    [
        478,
        "Card: Ditto (Original card: 'outside APR 1969')"
    ],
    [
        483,
        "Card: 'AT COMO'"
    ],
    [
        484,
        "Card: 'PARK ~'"
    ],
    [
        485,
        "Card: 'SUMMER'"
    ],
    [
        486,
        "Card: '1969'"
    ],
    [
        487,
        "Card: Ditto (Original card: '1969')"
    ],
    [
        489,
        "Card: 'KAREN\\'S 4th'"
    ],
    [
        490,
        "Card: 'B-D JUNE 30'"
    ],
    [
        491,
        "Card: '1969'"
    ],
    [
        492,
        "Card: Ditto (Original card: '1969')"
    ],
    [
        493,
        "Card: Ditto (Original card: '1969')"
    ],
    [
        500,
        "Card: 'View up 1st Portage'"
    ],
    [
        502,
        "Card: 'Our Island Camp'"
    ],
    [
        505,
        "Card: 'At CAMP'"
    ],
    [
        507,
        "Card: Ditto (Original card: 'With Fish Boiled')"
    ],
    [
        508,
        "Card: 'Me Cooking'"
    ],
    [
        509,
        "Card: Ditto (Original card: 'With Fish Boiled')"
    ],
    [
        510,
        "Card: 'A Rainy Day'"
    ],
    [
        512,
        "Card: Ditto (Original card: 'Fish'ing')"
    ],
    [
        513,
        "Card: Ditto (Original card: 'Fish'ing')"
    ],
    [
        514,
        "Card: Ditto (Original card: 'Fish'ing')"
    ],
    [
        515,
        "Card: 'The Last Portage'"
    ],
    [
        518,
        "Card: 'AIRPORT'"
    ],
    [
        519,
        "Card: Ditto (Original card: 'Fish'ing')"
    ],
    [
        521,
        "Card: Ditto (Original card: 'Karen & friends, Spring 1970')"
    ],
    [
        524,
        "Card: 'Mayo Clinic'"
    ],
    [
        525,
        "Card: 'Rochester, Minn.'"
    ],
    [
        526,
        "Card: Ditto (Original card: 'Karen &' (owner-confirmed); leads into 'Stephen in tub')"
    ],
    [
        527,
        "Card: 'Fall 1969'"
    ],
    [
        528,
        "Card: Ditto (Original card: 'Karen &' (owner-confirmed); leads into 'Stephen in tub')"
    ],
    [
        529,
        "Card: Ditto (Original card: 'Karen &' (owner-confirmed); leads into 'Stephen in tub')"
    ],
    [
        531,
        "Card: Ditto (Original card: 'Rochester, Minn.')"
    ],
    [
        544,
        "Card: Ditto (Original card: 'Birthday')"
    ],
    [
        545,
        "Card: Ditto (Original card: 'Birthday')"
    ],
    [
        546,
        "Card: Ditto (Original card: 'Birthday')"
    ],
    [
        550,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        552,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        553,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        554,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        555,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        558,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        559,
        "Card: Ditto (Original card: 'Stevie & Karen' (was misread 'Marie')"
    ],
    [
        562,
        "Card: 'Sister Bay, Wis'"
    ],
    [
        582,
        "Card: Ditto (Original card: 'SEAQUARIUM')"
    ],
    [
        585,
        "Card: Ditto (Original card: 'Porpose Show')"
    ],
    [
        586,
        "Card: Ditto (Original card: 'Porpose Show')"
    ],
    [
        587,
        "Card: Ditto (Original card: 'Porpose Show')"
    ],
    [
        595,
        "Card: Ditto (Original card: '\" from Causeway')"
    ],
    [
        603,
        "Card: Ditto (Original card: 'Everglades / Continued')"
    ],
    [
        606,
        "Card: Ditto (Original card: 'Deep Sea Fishing')"
    ],
    [
        609,
        "Card: Ditto (Original card: 'Catch')"
    ],
    [
        614,
        "Card: Ditto (Original card: 'Catch')"
    ],
    [
        616,
        "Card: Ditto (Original card: 'Miami Beach')"
    ],
    [
        617,
        "Card: Ditto (Original card: 'Miami Beach')"
    ],
    [
        618,
        "Card: Ditto (Original card: 'Miami Beach')"
    ],
    [
        620,
        "Card: Ditto (Original card: 'Miami Beach')"
    ],
    [
        621,
        "Card: Ditto (Original card: 'Miami Beach')"
    ],
    [
        623,
        "Card: Ditto (Original card: 'Miami Beach')"
    ],
    [
        629,
        "Card: Ditto (Original card: 'Fountain Blau')"
    ],
    [
        631,
        "Card: Ditto (Original card: 'Fountain Blau')"
    ],
    [
        634,
        "Card: Ditto (Original card: 'Dottie, Marilyn, & kids')"
    ],
    [
        639,
        "Card: Ditto (Original card: 'Karen & Friends off to School, Sep 70')"
    ],
    [
        640,
        "Card: Ditto (Original card: 'Karen & Friends off to School, Sep 70')"
    ],
    [
        642,
        "Card: Ditto (Original card: 'Karen and Steve, Sept 1970')"
    ],
    [
        644,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        645,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        646,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        647,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        652,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        653,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        655,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        656,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        657,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        658,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        659,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        660,
        "Card: Ditto (Original card: 'Sept 1970')"
    ],
    [
        663,
        "Card: Ditto (Original card: 'O'Brien State Park on the St. Croix River, Fall 1970')"
    ],
    [
        664,
        "Card: Ditto (Original card: 'O'Brien State Park on the St. Croix River, Fall 1970')"
    ],
    [
        666,
        "Card: Ditto (Original card: '1970')"
    ],
    [
        667,
        "Card: Ditto (Original card: '1970')"
    ],
    [
        669,
        "Card: Ditto (Original card: 'A Stroll thru St Anthony Park')"
    ],
    [
        671,
        "Card: Ditto (Original card: 'Park')"
    ],
    [
        672,
        "Card: Ditto (Original card: 'Park')"
    ],
    [
        676,
        "Card: Ditto (Original card: 'Karen & Steve at Commonwealth Christmas Party')"
    ],
    [
        677,
        "Card: Ditto (Original card: 'Karen & Steve at Commonwealth Christmas Party')"
    ],
    [
        679,
        "Card: Ditto (Original card: 'Karen & Steve at Commonwealth Christmas Party')"
    ],
    [
        681,
        "Card: Ditto (Original card: 'Karen In Wig')"
    ],
    [
        687,
        "Card: Ditto (Original card: 'Christmas 1970')"
    ],
    [
        688,
        "Card: Ditto (Original card: 'Christmas 1970')"
    ],
    [
        690,
        "Card: Ditto (Original card: 'Winter '70-71')"
    ],
    [
        691,
        "Card: Ditto (Original card: 'Winter '70-71')"
    ],
    [
        692,
        "Card: Ditto (Original card: 'Winter '70-71')"
    ],
    [
        693,
        "Card: Ditto (Original card: 'Winter '70-71')"
    ],
    [
        696,
        "Card: Ditto (Original card: 'St. Paul Conservatory, Como Park')"
    ],
    [
        697,
        "Card: Ditto (Original card: 'St. Paul Conservatory, Como Park')"
    ],
    [
        699,
        "Card: Ditto (Original card: 'Spring 1971')"
    ],
    [
        700,
        "Card: Ditto (Original card: 'Spring 1971')"
    ],
    [
        701,
        "Card: Ditto (Original card: 'Spring 1971')"
    ],
    [
        702,
        "Card: Ditto (Original card: 'Spring 1971')"
    ],
    [
        703,
        "Card: Ditto (Original card: 'Spring 1971')"
    ],
    [
        704,
        "Card: Ditto (Original card: 'Spring 1971')"
    ],
    [
        709,
        "Card: Ditto (Original card: 'Karen & Steve on Back Porch of Skillman Apt.')"
    ],
    [
        714,
        "Card: Ditto (Original card: 'Karen & Larry's House'. Karen = WENDEL')"
    ],
    [
        716,
        "Card: Ditto (Original card: 'I 80 across Wyoming')"
    ],
    [
        718,
        "Card: Ditto (Original card: 'Mountains of Eastern Utah')"
    ],
    [
        719,
        "Card: Ditto (Original card: 'Mountains of Eastern Utah')"
    ],
    [
        721,
        "Card: Ditto (Original card: 'Salt Lake City')"
    ],
    [
        725,
        "Card: Ditto (Original card: 'Temple at night')"
    ],
    [
        729,
        "Card: Ditto (Original card: 'The Great Salt Lake')"
    ],
    [
        730,
        "Card: Ditto (Original card: 'The Great Salt Lake')"
    ],
    [
        732,
        "Card: Ditto (Original card: 'Bonneville Salt Flats')"
    ],
    [
        733,
        "Card: Ditto (Original card: 'Bonneville Salt Flats')"
    ],
    [
        734,
        "Card: Ditto (Original card: 'Bonneville Salt Flats')"
    ],
    [
        735,
        "Card: Ditto (Original card: 'Bonneville Salt Flats')"
    ],
    [
        739,
        "Card: Ditto (Original card: 'Reno')"
    ],
    [
        741,
        "Card: Ditto (Original card: 'S.F. Bay Bridge')"
    ],
    [
        748,
        "Card: Ditto (Original card: 'Stanford Univ - Palo Alto')"
    ],
    [
        749,
        "Card: Ditto (Original card: 'Stanford Univ - Palo Alto')"
    ],
    [
        752,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        753,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        754,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        755,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        756,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        757,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        758,
        "Card: Ditto (Original card: 'Lake Tahoe')"
    ],
    [
        760,
        "Card: Ditto (Original card: 'Karen & Steve')"
    ],
    [
        761,
        "Card: Ditto (Original card: 'Karen & Steve')"
    ],
    [
        763,
        "Card: Ditto (Original card: 'Rainbow in Indiana')"
    ],
    [
        767,
        "Card: Ditto (Original card: 'House in St. Paul')"
    ],
    [
        768,
        "Card: Ditto (Original card: 'House in St. Paul')"
    ],
    [
        769,
        "Card: Ditto (Original card: 'House in St. Paul')"
    ],
    [
        773,
        "Card: Ditto (Original card: 'Littleton')"
    ],
    [
        774,
        "Card: Ditto (Original card: 'Littleton')"
    ],
    [
        775,
        "Card: Ditto (Original card: 'Littleton')"
    ],
    [
        777,
        "Card: Ditto (Original card: 'Sept Snow')"
    ],
    [
        779,
        "Card: Ditto (Original card: 'So. Platte River')"
    ],
    [
        781,
        "Card: Ditto (Original card: 'Karen & Steve sleeping together')"
    ],
    [
        783,
        "Card: Ditto (Original card: 'Karen's 1st Day of School (1st grade), at Normandy, Sept 1971')"
    ],
    [
        785,
        "Card: Ditto (Original card: 'Karen's 1st Day of School (1st grade), at Normandy, Sept 1971')"
    ],
    [
        788,
        "Card: Ditto (Original card: 'At Heritage Square, Denver, Fall 1971')"
    ],
    [
        803,
        "Card: Ditto (Original card: 'Bob & I in cockpit')"
    ],
    [
        807,
        "Card: Ditto (Original card: '1971')"
    ],
    [
        808,
        "Card: Ditto (Original card: '1971')"
    ],
    [
        809,
        "Card: Ditto (Original card: '1971')"
    ],
    [
        811,
        "Card: Ditto (Original card: 'Denver Zoo')"
    ],
    [
        812,
        "Card: Ditto (Original card: 'Denver Zoo')"
    ],
    [
        813,
        "Card: Ditto (Original card: 'Denver Zoo')"
    ],
    [
        815,
        "Card: Ditto (Original card: 'Denver Zoo')"
    ],
    [
        816,
        "Card: Ditto (Original card: 'Denver Zoo')"
    ],
    [
        819,
        "Card: Ditto (Original card: 'More Zoo')"
    ],
    [
        824,
        "Card: Ditto (Original card: 'On Narrow Gauge')"
    ],
    [
        826,
        "Card: Ditto (Original card: 'Camping with Nick and the Boys at Morley Gulch'. Nick = likely Dottie')"
    ],
    [
        837,
        "Card: Ditto (Original card: 'Camping with Family again at Molley Gulch')"
    ],
    [
        840,
        "Card: Ditto (Original card: 'Molley Gulch')"
    ],
    [
        841,
        "Card: Ditto (Original card: 'Molley Gulch')"
    ],
    [
        842,
        "Card: Ditto (Original card: 'Molley Gulch')"
    ],
    [
        848,
        "Card: Ditto (Original card: 'Karen the Brownie')"
    ],
    [
        858,
        "Card: Ditto (Original card: 'Karen the Ballerina')"
    ],
    [
        859,
        "Card: Ditto (Original card: 'Karen the Ballerina')"
    ],
    [
        860,
        "Card: Ditto (Original card: 'Karen the Ballerina')"
    ],
    [
        861,
        "Card: Ditto (Original card: 'Karen the Ballerina')"
    ],
    [
        864,
        "Card: Ditto (Original card: 'Greig's Visit on Thanksgiving 1972')"
    ],
    [
        867,
        "Card: Ditto (Original card: 'Ice Skating')"
    ],
    [
        868,
        "Card: Ditto (Original card: 'Ice Skating')"
    ],
    [
        869,
        "Card: Ditto (Original card: 'Ice Skating')"
    ],
    [
        870,
        "Card: Ditto (Original card: 'Ice Skating')"
    ],
    [
        871,
        "Card: Ditto (Original card: 'Ice Skating')"
    ],
    [
        872,
        "Card: Ditto (Original card: 'Ice Skating')"
    ],
    [
        874,
        "Card: Ditto (Original card: 'Christmas 1972')"
    ],
    [
        875,
        "Card: Ditto (Original card: 'Christmas 1972')"
    ],
    [
        876,
        "Card: Ditto (Original card: 'Christmas 1972')"
    ],
    [
        877,
        "Card: Ditto (Original card: 'Christmas 1972')"
    ],
    [
        878,
        "Card: Ditto (Original card: 'Christmas 1972')"
    ],
    [
        879,
        "Card: Ditto (Original card: 'Christmas 1972')"
    ],
    [
        882,
        "Card: Ditto (Original card: 'Breckenridge')"
    ],
    [
        883,
        "Card: Ditto (Original card: 'Breckenridge')"
    ],
    [
        884,
        "Card: Ditto (Original card: 'Breckenridge')"
    ],
    [
        885,
        "Card: Ditto (Original card: 'Breckenridge')"
    ],
    [
        886,
        "Card: Ditto (Original card: 'Breckenridge')"
    ],
    [
        888,
        "Card: Ditto (Original card: 'Easter 1973')"
    ],
    [
        889,
        "Card: Ditto (Original card: 'Easter 1973')"
    ],
    [
        891,
        "Card: Ditto (Original card: 'Easter 1973')"
    ],
    [
        892,
        "Card: Ditto (Original card: 'Easter 1973')"
    ],
    [
        893,
        "Card: Ditto (Original card: 'Easter 1973')"
    ],
    [
        898,
        "Card: Ditto (Original card: 'Communion')"
    ],
    [
        899,
        "Card: Ditto (Original card: 'Communion')"
    ],
    [
        901,
        "Card: Ditto (Original card: 'Communion')"
    ],
    [
        903,
        "Card: Ditto (Original card: 'Slip & Slide, Summer 1973')"
    ],
    [
        905,
        "Card: Ditto (Original card: '1973'; yellow Slip')"
    ],
    [
        912,
        "Card: Ditto (Original card: 'Summer 1973')"
    ],
    [
        913,
        "Card: Ditto (Original card: 'Summer 1973')"
    ],
    [
        914,
        "Card: Ditto (Original card: 'Summer 1973')"
    ],
    [
        916,
        "Card: Ditto (Original card: 'Dillon Reservoir')"
    ],
    [
        921,
        "Card: Ditto (Original card: 'Summer')"
    ],
    [
        927,
        "Card: Ditto (Original card: 'Karen / Aug. Camping Trip')"
    ],
    [
        928,
        "Card: Ditto (Original card: 'Karen / Aug. Camping Trip')"
    ],
    [
        930,
        "Card: Ditto (Original card: 'Great Sand Dunes')"
    ],
    [
        931,
        "Card: Ditto (Original card: 'Great Sand Dunes')"
    ],
    [
        932,
        "Card: Ditto (Original card: 'Great Sand Dunes')"
    ],
    [
        933,
        "Card: Ditto (Original card: 'Great Sand Dunes')"
    ],
    [
        934,
        "Card: Ditto (Original card: 'Great Sand Dunes')"
    ],
    [
        935,
        "Card: Ditto (Original card: 'Great Sand Dunes')"
    ],
    [
        937,
        "Card: Ditto (Original card: 'Mesa Verde')"
    ],
    [
        938,
        "Card: Ditto (Original card: 'Mesa Verde')"
    ],
    [
        939,
        "Card: Ditto (Original card: 'Mesa Verde')"
    ],
    [
        945,
        "Card: Ditto (Original card: 'Silverton in a low cloud')"
    ],
    [
        952,
        "Card: Ditto (Original card: 'Steve & friends off to school')"
    ],
    [
        961,
        "Card: Ditto (Original card: 'Christmas 1973 / Card Picture')"
    ],
    [
        963,
        "Card: Ditto (Original card: 'Stevie's Thanksgiving Play @ Hillcroft 1973')"
    ],
    [
        968,
        "Card: Ditto (Original card: '1973')"
    ],
    [
        971,
        "Card: Ditto (Original card: 'Winter 1974')"
    ],
    [
        972,
        "Card: Ditto (Original card: 'Winter 1974')"
    ],
    [
        976,
        "Card: Ditto (Original card: 'Hillcroft')"
    ],
    [
        979,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        980,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        981,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        982,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        983,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        984,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        985,
        "Card: Ditto (Original card: 'Feb 1974')"
    ],
    [
        995,
        "Card: Ditto (Original card: 'The Watergate')"
    ],
    [
        1000,
        "Card: Ditto (Original card: 'Mt Vernon')"
    ],
    [
        1001,
        "Card: Ditto (Original card: 'Mt Vernon')"
    ],
    [
        1004,
        "Card: Ditto (Original card: 'White House')"
    ],
    [
        1007,
        "Card: Ditto (Original card: 'Capitol')"
    ],
    [
        1014,
        "Card: Ditto (Original card: 'Spring 1974')"
    ],
    [
        1015,
        "Card: Ditto (Original card: 'Spring 1974')"
    ],
    [
        1016,
        "Card: Ditto (Original card: 'Spring 1974')"
    ],
    [
        1017,
        "Card: Ditto (Original card: 'Spring 1974')"
    ],
    [
        1073,
        "Card: Ditto (Original card: 'Royal Gorge')"
    ],
    [
        1074,
        "Card: Ditto (Original card: 'Royal Gorge')"
    ],
    [
        1075,
        "Card: Ditto (Original card: 'Royal Gorge')"
    ],
    [
        1076,
        "Card: Ditto (Original card: 'Royal Gorge')"
    ],
    [
        1077,
        "Card: Ditto (Original card: 'Royal Gorge')"
    ],
    [
        1091,
        "Card: Ditto (Original card: 'May 1976')"
    ],
    [
        1099,
        "Card: Ditto (Original card: '2-Bar')"
    ],
    [
        1107,
        "Card: Ditto (Original card: 'Elitch's')"
    ],
    [
        1109,
        "Card: Ditto (Original card: 'Camping @ Lake Dillon, July 1976')"
    ],
    [
        1111,
        "Card: Ditto (Original card: 'July 1976')"
    ],
    [
        1119,
        "Card: Ditto (Original card: 'Breckenridge 4th of July')"
    ],
    [
        1120,
        "Card: Ditto (Original card: 'Breckenridge 4th of July')"
    ],
    [
        1125,
        "Card: Ditto (Original card: 'King's Island')"
    ],
    [
        1127,
        "Card: Ditto (Original card: 'King's Island')"
    ],
    [
        1129,
        "Card: Ditto (Original card: 'King's Island')"
    ],
    [
        1131,
        "Card: Ditto (Original card: 'Hollenkamp's')"
    ]
]

def run():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cap_applied = cap_skipped = 0
    notes_applied = notes_skipped = 0

    for photo_id, new_val in CAPTION_UPDATES:
        row = db.execute("SELECT caption FROM photo WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            print(f"  WARN: photo {photo_id} not found"); continue
        if row["caption"] == new_val:
            cap_skipped += 1; continue
        if not DRY_RUN:
            db.execute("UPDATE photo SET caption=? WHERE id=?", (new_val, photo_id))
        cap_applied += 1

    for photo_id, new_val in NOTES_UPDATES:
        row = db.execute("SELECT notes FROM photo WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            print(f"  WARN: photo {photo_id} not found"); continue
        if row["notes"] == new_val:
            notes_skipped += 1; continue
        if not DRY_RUN:
            db.execute("UPDATE photo SET notes=? WHERE id=?", (new_val, photo_id))
        notes_applied += 1

    if not DRY_RUN:
        db.commit()

    tag = "[DRY RUN] " if DRY_RUN else ""
    print(f"{tag}Captions: {cap_applied} updated, {cap_skipped} already current")
    print(f"{tag}Notes:    {notes_applied} updated, {notes_skipped} already current")
    if DRY_RUN:
        print("No changes written.")

if __name__ == "__main__":
    print(f"DB: {DB_PATH}  mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    run()
