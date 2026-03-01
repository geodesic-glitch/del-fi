# Infrastructure & Utilities Log — Maplewood Neighborhood

Current status of roads, utilities, waterways, and public infrastructure. Updated from city data feeds, sensor reports, and community observations. As of Feb 19, 2026.

---

## Millbrook Creek — Current Level & Status

**Current reading (Feb 19, 2026 · 09:00):** 4.3 ft at Oak Street gauge (MILLBROOK-SENSOR)
**Status: Elevated. Receding slowly.**

Flood stage thresholds:
- Below 3.5 ft: Normal range, trail fully passable
- 3.5–4.5 ft: Elevated — lower trail (Sycamore underpass section) may be muddy; monitor
- 4.5–5.5 ft: Minor flood — lower trail flooded, Sycamore underpass submerged
- Above 5.5 ft: Moderate flood — trail closed from Sycamore to Maple Grove Rd; possible street flooding at Sycamore & Fern Ave (lowest point in neighborhood)

**Current trail status:** Lower Greenway trail (Sycamore to Maple Grove access) CLOSED — posted signs in place. Reopens when creek drops below 4.0 ft AND trail crew clears debris. Upper trail (Oak to Garfield access) open and passable.

**Recent level history:**
| Date | Peak Level | Status |
|------|-----------|--------|
| Feb 19 | 4.3 ft (receding) | Elevated |
| Feb 18 | 5.1 ft (peak ~06:00) | Minor flood |
| Feb 17 | 4.8 ft | Elevated/minor flood |
| Feb 16 | 3.8 ft | Elevated |
| Feb 14–15 | 5.4 ft (storm peak) | Minor flood |
| Feb 13 | 3.2 ft | Normal |
| Feb 7 | 3.0 ft | Normal |

Millbrook Creek historically floods the lower Greenway 4–8 times per winter, with moderate overbank flooding (affecting Sycamore St area) approximately 1–2 times per year. Major flooding reaching into residential yards is rare (last event: 2019 atmospheric river storm, 7.8 ft).

---

## Power Grid

**Current status: NORMAL — no known outages as of 09:00 Feb 19**

**Recent events:**
- **Feb 15 outage (03:45–10:30):** East grid substation fault. ~4,800 customers affected including most of Maplewood. Duration: ~7 hours. MAPLEWOOD-ORACLE ran on library battery backup; backup exhausted at ~09:15; node off 09:15–10:30. Grid restored by city electric crew.
- MAPLEWOOD-ORACLE battery backup: approximately 8 hours (tested Feb 15 — actual runtime was ~5.5 hours; likely reduced by cold temperatures and node transmit load). Consider this worst-case: 5–6 hours backup.

**Report outages:** cityelectric.gov/outage or (555) 800-5555.

---

## Road Conditions & Active Work

### Active Construction / Closures (as of Feb 19, 2026)

**Oak Street @ Millbrook Bridge — Lane restriction**
Right lane closed, eastbound only, between Maplewood Ave and Millbrook Rd for bridge deck repair. Scheduled: Dec 2025 – March 2026. Expect delays during morning commute (7–9am). Typical backup ~5–10 min.

**Sycamore St @ Fern Ave — Pothole repair pending**
Pothole reported Feb 12. City Works ticket #26-10943. Not yet repaired as of Feb 19. Caution: significant pothole in southbound lane adjacent to crosswalk. Report additional road hazards at cityworks.gov.

**Garfield Ave @ 52nd — Temporary signal timing change**
Signal timing adjusted through March 1 for increased pedestrian crossing time at the school crossing (related to Garfield Middle School's late winter schedule). Commute impact: minor, ~30 sec additional delay westbound AM.

### Sidewalk Conditions
Following the Feb 14–15 storm, several fallen branches were cleared from sidewalks on Maplewood Ave and Oak Street by city tree crew. Remaining hazard: uneven pavement on the east side of Maplewood Ave between 51st and 52nd — tree root heaving, reported to city, repair scheduled for spring.

---

## Internet & Telecommunications

**Known ISP issues (as of Feb 19):** No widespread outages reported.

Neighborhood fiber coverage (ISP1): Available throughout most of Maplewood except the 5300–5400 block east of Maplewood Ave (infrastructure upgrade planned Q3 2026).

The Maplewood Library provides free public WiFi (network: MPL-Public, no password). Range covers front steps and parking lot as well as interior.

---

## Water & Sewer

**Current status: Normal.**

Note: Homeowners with older homes (pre-1980) in Maplewood may have galvanized steel water supply pipes; if experiencing reduced water pressure or discolored water, contact the city water department. The neighborhood does not currently have any known main breaks.

**Catch basin clearing:** After the Feb 14–15 storm, city crews cleared debris-blocked catch basins on Sycamore St (2 blocked), Oak St (1 blocked), and Maplewood Ave (1 blocked) on Feb 16. If you observe a blocked drain during or after rain, report to cityworks.gov.

---

## Natural Gas

No known issues. Contact Regional Gas Co at (800) 555-3030 immediately for any gas odor — do not use electrical switches or phones near a suspected leak; exit the building first.

---

## MAPLEWOOD-ORACLE Node — Status

| Parameter | Current Value | Notes |
|-----------|--------------|-------|
| Power source | Grid (library AC) | Battery backup ~5–6 hrs |
| Battery backup | Charged | Post-outage, fully recharged |
| Uptime | Since Feb 16, 10:30 | ~3 days |
| EASTSIDE-RELAY link | Strong | Normal |
| GARFIELD-NODE link | Good | Normal |
| MILLBROOK-SENSOR link | Good | Normal |
| Message queue | Clear | — |

Node is maintained by the Maplewood Digital Commons working group. For technical issues, message the node or contact the library.
