# Roadmap

Development follows an **AGILE, iterative cycle**. Phase descriptions to be detailed as work
progresses; update status inline. ☑ done · ◐ in progress · ☐ not started.

| # | Phase                        | Status | Notes (as of 2026-10-18) |
|---|------------------------------|--------|-------|
| 1 | Business + Data Understanding | ☑ | Interview with En. Zamzuri (B1), product comparison, DOSM data — FYP1 |
| 2 | Product Design                | ☑ | Two-loop architecture, content layer, head-unit wiring + mount designs |
| 3 | Product Development           | ◐ | Done: desktop beta (2 detectors, steering, tracking, Malay voice, map), firmware written. Open: head unit build + ToF in the beta, ground-plane distance, manhole + more Malaysian hazards, Android app |
| 4 | Sketch Testing                | ◐ | Self-tests + desk tests done; controlled walk tests need the head unit |
| 5 | Beta Deployment               | ☐ | Field trial / UAT with visually impaired users |
| 6 | Review & Improvement          | ☐ | Feed walk-test + UAT findings back |

## Expected outcomes
- **Working wearable prototype** — detects common obstacles in real time; usable daily.
- **High-accuracy depth estimation** — accurate distance to nearest forward hazard via ToF.
- **Locally-relevant product** — usable in the Malaysian context, Bahasa Melayu.
- **Low cost vs commercial** — far cheaper than existing devices (≈ RM 2,900–21,000 range).
- **True navigation logic** — helps the user navigate safely out of hazardous road situations.

## Future work (beyond FYP — for the viva "future work" slide)

- **Adventure / follow-me pacman map.** Today the map is ego-centric: a fresh snapshot every
  4 s with the user fixed at bottom-centre. The upgrade is a *world-frame* map that remembers
  obstacles (with decay for moving ones) while the pacman character moves as the user walks
  and turns, re-planning BFS from its current cell toward a goal heading.
  - Needs a pose source: an IMU on the head unit (e.g. BNO085) + step counting = pedestrian
    dead reckoning (good for ~10–20 m before drift). Phone sensors alone track the phone, not
    the head. Visual odometry is out of scope.
  - Demo path: simulate walking with WASD on the desktop beta first (same world-map + replanning
    code), then swap the keys for the IMU.
