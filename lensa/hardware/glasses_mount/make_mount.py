"""LENSA glasses mount -> STL files (printable parts + assembled + exploded preview).

Edit the numbers below after measuring your real parts (all mm), then run:
    pip install manifold3d numpy
    python make_mount.py
Printable STLs are written in print orientation (flat face on the bed).
lensa_assembly.stl / lensa_exploded.stl are previews only - open them in Tinkercad, don't print them.

World axes (assembly files): the wearer looks toward -Y, +Z is up, wearer's RIGHT is -X.
"""
import struct
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold

# ---------------- MEASURE & EDIT: electronics ----------------
BOARD_L, BOARD_W = 64.0, 28.0   # ESP32-S3 board length x width
PLATE_DEPTH = 18.0              # front face -> open back; = camera front to header-pin tips + ~2
CAM_X = 18.0                    # antenna-end outer edge -> camera centre
CAM_D = 9.0                     # camera hole diameter
TOF_GAP = 20.0                  # camera centre -> ToF chip centre (toward the USB end)
TOF_WIN = (9.0, 6.0)            # ToF window (w, h) at the inside; front is chamfered 45 deg
USB_SLOT = (24.0, 10.0)         # USB-C slot on the USB end (height, depth); the 5V+GND wires leave here too
USB_SLOT_Y = 3.0                # slot starts this far behind the front face

BATT = (39.0, 19.0, 9.0)        # LiPo L x W x H
IP5310_H = 5.0                  # IP5310 module height (sits on top of battery)
SWITCH_SLOT = (9.0, 4.0)        # switch knob slot (length, height)
CHARGE_SLOT = (10.0, 4.0)       # IP5310 USB-C slot (width, height)
CABLE_D = 4.0                   # pod cable hole

# ---------------- MEASURE & EDIT: glasses frame ----------------
LENS = (50.0, 36.0)             # lens opening w x h
RIM = 3.0                       # rim width around each lens
FRAME_T = 4.0                   # frame thickness front-to-back (= bridge depth the clip grips)
BRIDGE_LEN = 18.0               # gap between the lens openings at the bridge
BRIDGE_H = 5.0                  # bridge bar height (the clip grips this)
ARM = (5.0, 10.0)               # temple arm thickness x height (the pod clips grip this)
TEMPLE_LEN = 160.0              # frame front -> end of temple (strap slot at the end)
SPLAY = 6.0                     # temples angle outward (deg) so they don't squeeze the head
POD_Y = 105.0                   # frame front -> ear end of the battery pod (behind the ear)
LUG_LIFT = 5.0                  # hinge axis height above the bridge clip; raise if the plate hits the rims
TILT = 10.0                     # plate tilt down (deg) shown in the assembly; the hinge allows 0-20

# ---------------- fixed design rules ----------------
PLAY = 1.0          # total clearance around a part
WALL = 2.0
FRONT_WALL = 1.5
ANT_WALL = 1.0      # thin wall over the Wi-Fi antenna
FIT = 0.15          # gap per side for press-fits, clips and sockets
LIP_H = 2.0         # how far a lid's lip drops into its box
M2 = 2.2            # M2 clearance hole
SEG = 48            # circle smoothness


def box(x0, y0, z0, x1, y1, z1):
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def cyl_x(x0, x1, y, z, d):  # cylinder along X
    return Manifold.cylinder(x1 - x0, d / 2, d / 2, SEG).rotate([0, 90, 0]).translate([x0, y, z])


def cyl_y(y0, y1, x, z, d):  # cylinder along Y
    return Manifold.cylinder(y1 - y0, d / 2, d / 2, SEG).rotate([-90, 0, 0]).translate([x, y0, z])


def union(parts):
    out = parts[0]
    for p in parts[1:]:
        out += p
    return out


def on_bed(m):
    lo = np.array(m.bounding_box()[:3])
    return m.translate((-lo).tolist())


def rot(axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


# ======== front plate (local: x = USB end 0 -> antenna end PL, y = depth, front face y=0, z up) ========
PL = WALL + BOARD_L + PLAY + ANT_WALL          # plate length
PH = WALL + BOARD_W + PLAY + WALL              # plate height
ZC = PH / 2
CAM_CX = PL - CAM_X                            # the camera sits near the antenna end of the board
TOF_CX = CAM_CX - TOF_GAP
PCB_Y = FRONT_WALL + 0.1 + 5.0 + 3.2               # camera + module stand in front of the PCB
TAB_W, TAB_T, AXIS_DROP = 8.0, 6.0, 4.0         # hinge tab at the BACK of the plate: width, thickness, axis drop
PLATE_AXIS = np.array([PL / 2, PLATE_DEPTH - TAB_T / 2, -AXIS_DROP])


def front_plate():
    body = box(0, 0, 0, PL, PLATE_DEPTH, PH)
    body -= box(WALL, FRONT_WALL, WALL, PL - ANT_WALL, PLATE_DEPTH + 1, PH - WALL)
    body -= cyl_y(-1, FRONT_WALL + 1, CAM_CX, ZC, CAM_D)
    w, h = TOF_WIN                      # ToF window: frustum, 45 deg chamfer opening toward the front
    grow = 2 * FRONT_WALL
    win = Manifold.extrude(CrossSection.square([w + grow, h + grow], center=True), FRONT_WALL + 0.02,
                           scale_top=[w / (w + grow), h / (h + grow)])
    body -= win.rotate([-90, 0, 0]).translate([TOF_CX, -0.01, ZC])
    sh, sd = USB_SLOT
    body -= box(-1, USB_SLOT_Y, ZC - sh / 2, WALL + 1, USB_SLOT_Y + sd, ZC + sh / 2)
    # hinge tab under the back edge: round end (swings clear of the clip) + 45 deg gusset (prints without support)
    ay, az = PLATE_AXIS[1], PLATE_AXIS[2]
    prof = CrossSection([[(ay - 10, 0.5), (ay - 3, az - 3), (PLATE_DEPTH, az), (PLATE_DEPTH, 0.5)]])
    tab = Manifold.extrude(prof, TAB_W).transform(np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], float))
    tab = tab.translate([PL / 2 - TAB_W / 2, 0, 0])
    tab += cyl_x(PL / 2 - TAB_W / 2, PL / 2 + TAB_W / 2, ay, az, TAB_T)
    tab -= cyl_x(PL / 2 - TAB_W, PL / 2 + TAB_W, ay, az, M2)
    return body + tab


def lid(outer_l, outer_w, in_x0, in_x1, in_y0, in_y1, vents, ends_only=False):
    """Flat cover + press-fit lip that drops into the opening.
    ends_only: lip is just two short blocks on the end walls (keeps clear of the ESP32 header rows)."""
    t, lip_t = 1.5, 1.2
    m = box(0, 0, 0, outer_l, outer_w, t)
    if ends_only:
        y0, y1 = outer_w / 2 - 7.5, outer_w / 2 + 7.5
        lip = box(in_x0 + FIT, y0, t, in_x0 + FIT + lip_t, y1, t + LIP_H)
        lip += box(in_x1 - FIT - lip_t, y0, t, in_x1 - FIT, y1, t + LIP_H)
    else:
        lip = box(in_x0 + FIT, in_y0 + FIT, t, in_x1 - FIT, in_y1 - FIT, t + LIP_H)
        lip -= box(in_x0 + FIT + lip_t, in_y0 + FIT + lip_t, t - 1, in_x1 - FIT - lip_t, in_y1 - FIT - lip_t, t + LIP_H + 1)
    m += lip
    for vx in vents:
        m -= box(vx, outer_w / 2 - 7.5, -1, vx + 2, outer_w / 2 + 7.5, t + 1)
    return m


def plate_cover():
    vents = [PL * f for f in (0.25, 0.4, 0.55, 0.7)]   # none over the antenna end
    return lid(PL, PH, WALL, PL - ANT_WALL, WALL, PH - WALL, vents, ends_only=True)


def cover_in_place():  # plate coords
    return plate_cover().rotate([90, 0, 0]).translate([0, PLATE_DEPTH + 1.5, 0])


def esp32_dummy():  # plate coords; camera faces front, board lies sideways
    x0, x1 = WALL + 0.5, WALL + 0.5 + BOARD_L
    z0, z1 = ZC - BOARD_W / 2, ZC + BOARD_W / 2
    pcb_y = PCB_Y
    parts = [box(x0, pcb_y, z0, x1, pcb_y + 1.6, z1),                                   # PCB
             box(x1 - 25.5, pcb_y - 3.2, ZC - 9, x1, pcb_y, ZC + 9),                     # ESP32-S3 module
             box(CAM_CX - 4.5, FRONT_WALL + 0.1, ZC - 4.5, CAM_CX + 4.5, pcb_y - 3.2, ZC + 4.5),  # OV5640
             cyl_y(0.3, FRONT_WALL + 0.1, CAM_CX, ZC, 7.0),                              # lens in the hole
             box(x0 + 1.5, pcb_y + 1.6, z0 + 0.5, x1 - 1.5, PLATE_DEPTH - LIP_H, z0 + 3),  # header rows
             box(x0 + 1.5, pcb_y + 1.6, z1 - 3, x1 - 1.5, PLATE_DEPTH - LIP_H, z1 - 0.5)]
    for zc in (ZC - 6, ZC + 6):                                                          # 2 USB-C ports
        parts.append(box(x0, pcb_y - 3.2, zc - 4.5, x0 + 7.5, pcb_y, zc + 4.5))
    return parts


def tof_dummy():  # plate coords; foam-taped onto the ESP32, chip right behind its window
    chip_y = FRONT_WALL + 0.1
    return [box(TOF_CX - 13, chip_y + 1.75, ZC - 8, TOF_CX + 13, chip_y + 3.35, ZC + 8),
            box(TOF_CX - 3.2, chip_y, ZC - 1.5, TOF_CX + 3.2, chip_y + 1.75, ZC + 1.5)]


# ======== bridge mount (local: x along the bridge, channel open at the bottom) ========
LUG_T = 3.0
MOUNT_L = TAB_W + 0.4 + 2 * LUG_T + 2
MOUNT_D = max(FRAME_T + 2 * FIT + 2 * WALL, TAB_T)
MOUNT_H = BRIDGE_H + FIT + WALL
MOUNT_AXIS_Z = MOUNT_H + LUG_LIFT


def bridge_mount():
    bd = FRAME_T + 2 * FIT
    m = box(0, 0, 0, MOUNT_L, MOUNT_D, MOUNT_H)
    m -= box(-1, (MOUNT_D - bd) / 2, -1, MOUNT_L + 1, (MOUNT_D + bd) / 2, BRIDGE_H + FIT)
    yc = MOUNT_D / 2
    for x0 in (1, MOUNT_L - 1 - LUG_T):
        lug = box(x0, yc - TAB_T / 2, MOUNT_H - 0.5, x0 + LUG_T, yc + TAB_T / 2, MOUNT_AXIS_Z)
        lug += cyl_x(x0, x0 + LUG_T, yc, MOUNT_AXIS_Z, TAB_T)
        m += lug
    m -= cyl_x(-1, MOUNT_L + 1, yc, MOUNT_AXIS_Z, M2)
    return m


# ======== battery pod (local: x ear end 0 -> rear end, y head side 0 -> outer face, z up; RIGHT temple) ========
POD_L = BATT[0] + PLAY + 2 * WALL
POD_W = BATT[1] + PLAY + 2 * WALL
POD_H = BATT[2] + IP5310_H + PLAY + WALL + LIP_H    # bottom is the lid; its lip ring takes LIP_H
CH_T, CH_H, CLIP_T = ARM[0] + 2 * FIT, ARM[1] + 2 * FIT, 1.5   # arm channel in the clips


def battery_pod():
    m = box(0, 0, 0, POD_L, POD_W, POD_H)
    m -= box(WALL, WALL, -1, POD_L - WALL, POD_W - WALL, POD_H - WALL)
    top = POD_H - WALL
    sl, sh = SWITCH_SLOT
    m -= box(WALL, POD_W - WALL - 1, top - 6.5, WALL + sl, POD_W + 1, top - 6.5 + sh)
    cw, ch = CHARGE_SLOT
    m -= box(POD_L - WALL - 1, POD_W / 2 - cw / 2, top - 1 - ch, POD_L + 1, POD_W / 2 + cw / 2, top - 1)
    m -= cyl_x(-1, WALL + 1, POD_W / 2, top - 2.5, CABLE_D)
    for x0 in (POD_L * 0.18, POD_L * 0.64):        # two C-clips; the temple arm snaps in from below
        x1, zt = x0 + 8, POD_H - CLIP_T
        clip = box(x0, -(CH_T + CLIP_T), zt, x1, 0.01, POD_H)                       # top bar
        clip += box(x0, -(CH_T + CLIP_T), zt - CH_H - CLIP_T, x1, -CH_T, POD_H)      # bar on the head side
        clip += box(x0, -(CH_T + CLIP_T), zt - CH_H - CLIP_T, x1, -(CH_T - 1.0), zt - CH_H)  # 1 mm snap lip
        m += clip
    return m


def pod_lid():
    return lid(POD_L, POD_W, WALL, POD_L - WALL, WALL, POD_W - WALL, [])


def pod_dummies():  # pod coords
    batt = box(WALL + 0.5, WALL + 0.5, LIP_H, WALL + 0.5 + BATT[0], WALL + 0.5 + BATT[1], LIP_H + BATT[2])
    ip = box(POD_L - WALL - 0.5 - 26, WALL + 0.5, LIP_H + BATT[2], POD_L - WALL - 0.5, WALL + 0.5 + 17,
             LIP_H + BATT[2] + IP5310_H)
    return [batt, ip]


# ======== glasses frame (world coords; lens centre at the origin) ========
LENS_CX = BRIDGE_LEN / 2 + RIM + LENS[0] / 2
BRIDGE_Z0 = LENS[1] / 2 - 1 - BRIDGE_H
ARM_X = LENS_CX + LENS[0] / 2 + RIM            # temple centre line, at the outer rim edge
ARM_Z = LENS[1] / 2 - 8                        # temple centre height
EP_WALL = 2.5                                  # endpiece wall around the temple socket


def splay(m, side):  # side -1 = wearer's right (-X), +1 = left
    piv = [side * ARM_X, FRAME_T, ARM_Z]
    return m.translate([-v for v in piv]).rotate([0, 0, -side * SPLAY]).translate(piv)


def splay_pt(p, side):
    piv = np.array([side * ARM_X, FRAME_T, ARM_Z])
    return rot("z", -side * SPLAY) @ (np.asarray(p, float) - piv) + piv


def frame_front():
    r = 8.0
    inner = CrossSection.square([LENS[0] - 2 * r, LENS[1] - 2 * r], center=True).offset(
        r, JoinType.Round, circular_segments=SEG)
    outer = inner.offset(RIM, JoinType.Round, circular_segments=SEG)
    ring = Manifold.extrude(outer - inner, FRAME_T).rotate([90, 0, 0]).translate([0, FRAME_T, 0])
    m = ring.translate([-LENS_CX, 0, 0]) + ring.translate([LENS_CX, 0, 0])
    gap = LENS_CX - LENS[0] / 2
    m += box(-gap, 0, BRIDGE_Z0, gap, FRAME_T, BRIDGE_Z0 + BRIDGE_H)          # straight bridge bar
    at, ah = ARM
    for s in (-1, 1):
        x = s * ARM_X
        ep = box(x - at / 2 - FIT - EP_WALL, 0, ARM_Z - ah / 2 - FIT - EP_WALL,
                 x + at / 2 + FIT + EP_WALL, 13, ARM_Z + ah / 2 + FIT + EP_WALL)      # endpiece
        sock = box(x - at / 2 - FIT, FRAME_T, ARM_Z - ah / 2 - FIT, x + at / 2 + FIT, 20, ARM_Z + ah / 2 + FIT)
        m += ep - splay(sock, s)                                                     # glued temple socket
        pad = box(gap - RIM + 0.5, 0, -10, gap - 0.5, 10, 0)                         # nose pad
        m += pad if s > 0 else pad.mirror([1, 0, 0])
    return m


def temple(side=0):
    at, ah = ARM
    m = box(-at / 2, FRAME_T + FIT * 2, ARM_Z - ah / 2, at / 2, TEMPLE_LEN, ARM_Z + ah / 2)
    m -= box(-at, TEMPLE_LEN - 9, ARM_Z - 3.5, at, TEMPLE_LEN - 6, ARM_Z + 3.5)       # strap slot
    if side == 0:
        return m
    return splay(m.translate([side * ARM_X, 0, 0]), side)


# ======== placing parts on the frame ========
MOUNT_OFS = np.array([-MOUNT_L / 2, FRAME_T / 2 - MOUNT_D / 2, BRIDGE_Z0 - FIT])
AXIS_W = MOUNT_OFS + np.array([MOUNT_L / 2, MOUNT_D / 2, MOUNT_AXIS_Z])   # hinge axis in world


def place_mount(m):
    return m.translate(MOUNT_OFS.tolist())


def place_plate(m, tilt=TILT):
    return (m.translate((AXIS_W - PLATE_AXIS).tolist()).translate((-AXIS_W).tolist())
            .rotate([tilt, 0, 0]).translate(AXIS_W.tolist()))


def plate_pt(p, tilt=TILT):
    return rot("x", tilt) @ (np.asarray(p, float) - PLATE_AXIS) + AXIS_W


POD_OFS = np.array([-ARM_X - CH_T / 2, POD_Y, ARM_Z - (POD_H - CLIP_T - CH_H / 2)])


def place_pod(m):  # local x -> +Y, local y -> -X (outward on the right), then follow the splayed temple
    return splay(m.rotate([0, 0, 90]).translate(POD_OFS.tolist()), -1)


def pod_pt(p):
    x, y, z = p
    return splay_pt(np.array([-y, x, z]) + POD_OFS, -1)


def tube(points, d=2.5):
    balls = [Manifold.sphere(d / 2, 16).translate(list(map(float, p))) for p in points]
    return union([(a + b).hull() for a, b in zip(balls, balls[1:])])


def cable():
    """5V + GND pair: out of the plate's USB slot, down past the endpiece, along the right temple, into the pod."""
    out_x = -ARM_X - ARM[0] / 2 - 1.3           # just outside the temple's outer face
    pts = [plate_pt([-1, USB_SLOT_Y + 8, ZC + 9]),
           np.array([-ARM_X + 7, -2, LENS[1] / 2 + 12]),
           np.array([-ARM_X - 7, 10, ARM_Z + 9]),
           splay_pt([out_x, 25, ARM_Z + 2], -1),
           splay_pt([out_x, POD_Y - 6, ARM_Z + 2], -1),
           pod_pt([-1, POD_W / 2, POD_H - WALL - 2.5])]
    return tube(pts)


def assembly(exploded=False):
    e = (lambda v: v) if exploded else (lambda v: [0, 0, 0])
    plate_up, pod_out = e([0, -30, 45]), e([-35, 30, 0])
    parts = [frame_front(),
             temple(-1).translate(e([0, 30, 0])), temple(1).translate(e([0, 30, 0])),
             place_mount(bridge_mount()).translate(e([0, 0, 18])),
             place_plate(front_plate()).translate(plate_up),
             place_plate(cover_in_place().translate(e([0, 50, 0]))).translate(plate_up),
             place_pod(battery_pod()).translate(pod_out),
             place_pod(pod_lid().translate([0, 0, -1.5]).translate(e([0, 0, -30]))).translate(pod_out)]
    if exploded:  # show what goes inside
        parts += [place_plate(d.translate([0, 25, 0])).translate(plate_up) for d in esp32_dummy() + tof_dummy()]
        parts += [place_pod(d.translate([0, 0, -20])).translate(pod_out) for d in pod_dummies()]
    else:
        parts.append(cable())
    return parts


def fit_check():
    """Self-check: real parts fit their boxes, everything mounts on the frame, nothing overlaps."""
    def clear(name, a, b):
        v = (a ^ b).volume()
        assert v < 1e-3, f"{name} collide by {v:.2f} mm3"

    plate, cover = front_plate(), cover_in_place()
    esp, tof = union(esp32_dummy()), union(tof_dummy())
    clear("ESP32/plate", esp, plate); clear("ESP32/cover", esp, cover)
    clear("ToF/plate", tof, plate); clear("ToF/cover", tof, cover); clear("ToF/ESP32", tof, esp)
    clear("USB-C plug path", box(-5, PCB_Y - 3.2, ZC - 10.5, WALL + 0.5, PCB_Y, ZC + 10.5), plate)
    clear("ToF line of sight", box(TOF_CX - 3.2, -1, ZC - 1.5, TOF_CX + 3.2, 1.7, ZC + 1.5), plate)
    pod, plid = battery_pod(), pod_lid().translate([0, 0, -1.5])
    for i, d in enumerate(pod_dummies()):
        clear(f"pod part {i}/pod", d, pod); clear(f"pod part {i}/lid", d, plid)

    frame, tr, tl = frame_front(), temple(-1), temple(1)
    mount = place_mount(bridge_mount())
    wpod = place_pod(pod) + place_pod(plid)
    clear("bridge clip/frame", mount, frame)
    clear("right temple/frame", tr, frame); clear("left temple/frame", tl, frame)
    clear("pod/right temple", wpod, tr); clear("pod/frame", wpod, frame)
    for t in (0.0, TILT, 20.0):  # the whole tilt range swings clear
        p = place_plate(plate + cover, t)
        clear(f"plate@{t:.0f}deg/frame", p, frame); clear(f"plate@{t:.0f}deg/clip", p, mount)
    c = cable()
    for name, other in [("frame", frame), ("temple", tr), ("plate", place_plate(plate + cover)),
                        ("pod", wpod), ("clip", mount)]:
        clear(f"cable/{name}", c, other)


def write_stl(parts, path):
    tris = []
    for m in parts if isinstance(parts, list) else [parts]:
        mesh = m.to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3]
        tris.append(v[np.asarray(mesh.tri_verts)])
    t = np.concatenate(tris)
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3), ("c", "<f4", 3), ("x", "<u2")])
    rec["n"], rec["a"], rec["b"], rec["c"] = n, a, b, c
    with open(path, "wb") as f:
        f.write(b"LENSA glasses mount".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())


PARTS = {   # printable, in print orientation
    "front_plate": lambda: on_bed(front_plate().rotate([90, 0, 0])),   # front face down
    "front_plate_cover": plate_cover,
    "bridge_mount": bridge_mount,
    "battery_pod": lambda: on_bed(battery_pod().rotate([180, 0, 0])),  # top face down
    "battery_pod_lid": pod_lid,
    "glasses_front": lambda: on_bed(frame_front().rotate([90, 0, 0])),  # front face down
    "temple_arm_x2": lambda: on_bed(temple().rotate([0, 90, 0])),       # lying on its side; print two
}

if __name__ == "__main__":
    out = Path(__file__).parent
    fit_check()
    for name, fn in PARTS.items():
        m = fn()
        assert m.status().name == "NoError" and m.volume() > 0, name
        bb = m.bounding_box()
        write_stl(m, out / f"{name}.stl")
        print(f"{name:18s} {bb[3]-bb[0]:6.1f} x {bb[4]-bb[1]:6.1f} x {bb[5]-bb[2]:5.1f} mm  {m.volume()/1000:5.1f} cm3")
    for name, exploded in (("lensa_assembly", False), ("lensa_exploded", True)):
        parts = assembly(exploded)
        write_stl(parts, out / f"{name}.stl")
        lo = np.min([p.bounding_box()[:3] for p in parts], axis=0)
        hi = np.max([p.bounding_box()[3:] for p in parts], axis=0)
        print(f"{name:18s} {hi[0]-lo[0]:6.1f} x {hi[1]-lo[1]:6.1f} x {hi[2]-lo[2]:5.1f} mm  (preview only)")
