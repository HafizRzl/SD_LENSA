"""LENSA "Shield" concept - wraparound sports-shield glasses with everything hidden inside.

    camera + ToF : sensor bay in the centre of the brow, behind two small windows
    ESP32 board  : inside the RIGHT arm (lid on the outer face, USB-C at the back)
    battery      : inside the LEFT arm with the IP5310 + switch (balances the right arm)
    wires        : run through a channel inside the brow - nothing outside the frame

Needs a camera with a longer ribbon: OV5640, 24-pin, ~150 mm FPC (same connector as the Freenove camera).

Edit the numbers, then run (from this folder):
    pip install manifold3d numpy
    python make_shield.py
World axes: the wearer looks toward -Y, +Z is up (eye line z=0), wearer's RIGHT is -X.
"""
import sys
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "glasses_mount"))
from make_mount import box, cyl_y, on_bed, union, write_stl  # noqa: E402  (shared helpers)

# ---------------- MEASURE & EDIT ----------------
BOARD_L, BOARD_W = 64.0, 28.0   # ESP32-S3 board; a smaller board (e.g. XIAO 21 x 17.5) shrinks the arms
BATT = (39.0, 19.0, 9.0)        # LiPo L x H x T (stands on its edge in the left arm)
IP5310 = (26.0, 17.0, 5.0)      # IP5310 L x H x T
TOF_BOARD = (26.0, 16.0)        # VL53L8CX breakout w x h
CAM_BOX = 8.5                   # OV5640 module size
SWITCH_SLOT = (9.0, 4.0)        # switch knob slot under the left arm (length, width)

WRAP_R = 110.0                  # shield curve radius seen from above (smaller = more wrap)
HALF_ARC = 70.0                 # half the brow length, measured along the curve
BROW_Z = (10.0, 31.5)           # brow bottom / top height above the eye line
BROW_D = 11.0                   # brow depth front-to-back
LENS_BOTTOM = -24.0             # lens lower edge below the eye line
CAM_XW, TOF_XW = 6.0, -13.0     # camera / ToF chip centre, sideways from the nose (ToF toward the ESP32 side)
SPLAY = 9.0                     # arms angle outward (deg)
ARM_PIVOT = (64.0, 26.0, 2.0)   # arm front-inner corner (x out from centre, y back, z up)
ARM_LEN = 150.0                 # arm length to the strap slot end

# ---------------- design rules ----------------
FIT, PLAY, WALL = 0.15, 1.0, 1.5
HOUSE_T, HOUSE_H = 13.5, BOARD_W + PLAY + 2 * 1.5 + 2.0   # arm housing thickness, height
HOUSE_L = 2.0 + BOARD_L + PLAY + 1.5 + 1.5                # front wall .. board .. rear wall
TAPER = 20.0
TUN = (4.5, 7.5, BROW_Z[0] + 4.0, BROW_Z[1] - 1.5)        # wire channel in the brow: y0, y1, z0, z1
GROOVE = (1.5, 3.1, 3.0)                                  # lens groove: y0, y1, depth
BAY = (-30.0, 12.0, BROW_Z[0] + 3.5, BROW_Z[1] - 1.5)     # sensor bay (flat x0, x1, z0, z1)
BAY_ZC = (BAY[2] + BAY[3]) / 2
X_P, Y_P, Z0 = ARM_PIVOT


# ---------- the wrap: everything is drawn flat (x along the curve, y = depth) then bent ----------
def bend(m, seg=1.5):
    def f(v):
        x, y, z = v[:, 0], v[:, 1], v[:, 2]
        t = x / WRAP_R
        return np.stack([(WRAP_R - y) * np.sin(t), WRAP_R - (WRAP_R - y) * np.cos(t), z], 1)
    return m.refine_to_length(seg).warp_batch(f)


def wall_y(x, depth):  # world Y of the curved surface `depth` behind the front, at world X
    r = WRAP_R - depth
    return WRAP_R - np.sqrt(r * r - x * x)


# camera / ToF stand straight forward, just behind the curved front wall
CAM_Y = max(wall_y(CAM_XW - CAM_BOX / 2, 1.5), wall_y(CAM_XW + CAM_BOX / 2, 1.5)) + 0.15
TOF_PCB_Y = max(wall_y(TOF_XW - TOF_BOARD[0] / 2, 1.5), wall_y(TOF_XW + TOF_BOARD[0] / 2, 1.5)) + 0.15
TOF_CHIP_Y = TOF_PCB_Y - 1.75


def brow():
    zb, zt = BROW_Z
    m = box(-HALF_ARC, 0, zb, HALF_ARC, BROW_D, zt)
    for s in (-1, 1):                                                     # nose pads, behind the lens
        m += box(min(s * 7, s * 10), 4, -14, max(s * 7, s * 10), 14, zb + 0.5)
    m -= box(-HALF_ARC - 1, GROOVE[0], zb - 1, HALF_ARC + 1, GROOVE[1], zb + GROOVE[2])   # lens groove
    m -= box(-HALF_ARC - 1, TUN[0], TUN[2], HALF_ARC + 1, TUN[1], TUN[3])                 # wire channel
    m -= box(BAY[0], 1.5, BAY[2], BAY[1], BROW_D + 1, BAY[3])                             # sensor bay
    m = bend(m)
    m -= cyl_y(-2, CAM_Y, CAM_XW, BAY_ZC, 9.0)                                            # camera hole
    w, h, d = 6.4 + 1, 3.0 + 1, TOF_CHIP_Y + 1                  # ToF window: opens 25 deg each side
    grow = 2 * d * np.tan(np.radians(25))
    win = Manifold.extrude(CrossSection.square([w + grow, h + grow], center=True), d,
                           scale_top=[w / (w + grow), h / (h + grow)])
    return m - win.rotate([-90, 0, 0]).translate([TOF_XW, -1, BAY_ZC])


def bay_cover():
    x0, x1, z0, z1 = BAY[0] + FIT, BAY[1] - FIT, BAY[2] + FIT, BAY[3] - FIT
    m = box(x0, BROW_D - 1.5, z0, x1, BROW_D, z1)
    m += box(x0, BROW_D - 3.5, BAY_ZC - 3, x0 + 1.2, BROW_D - 1.5, BAY_ZC + 3)           # press-fit lips
    m += box(x1 - 1.2, BROW_D - 3.5, BAY_ZC - 3, x1, BROW_D - 1.5, BAY_ZC + 3)
    return bend(m, 1.0)


def lens_2d():
    w, top, bot, r = HALF_ARC - 1, BROW_Z[0] + GROOVE[2] - 0.5, LENS_BOTTOM, 14.0
    body = CrossSection.square([2 * w - 2 * r, top - bot - 2 * r], center=True).offset(
        r, JoinType.Round, circular_segments=48).translate([0, (top + bot) / 2])
    body = body + CrossSection.square([2 * w, (top - bot) / 2]).translate([-w, (top + bot) / 2])
    notch = CrossSection.square([22 - 16, 40 - 16], center=True).offset(
        8, JoinType.Round, circular_segments=48).translate([0, -6 - 20])
    return body - notch


def lens():
    t = GROOVE[1] - GROOVE[0] - 0.1
    flat = Manifold.extrude(lens_2d(), t).rotate([90, 0, 0]).translate([0, GROOVE[0] + 0.05 + t, 0])
    return bend(flat)


def bay_dummies():
    cam = box(CAM_XW - CAM_BOX / 2, CAM_Y, BAY_ZC - CAM_BOX / 2, CAM_XW + CAM_BOX / 2, CAM_Y + 5, BAY_ZC + CAM_BOX / 2)
    cam += cyl_y(wall_y(CAM_XW, 0) + 0.3, CAM_Y, CAM_XW, BAY_ZC, 7.0)
    tw, th = TOF_BOARD
    tof = box(TOF_XW - tw / 2, TOF_PCB_Y, BAY_ZC - th / 2, TOF_XW + tw / 2, TOF_PCB_Y + 1.6, BAY_ZC + th / 2)
    chip = box(TOF_XW - 3.2, TOF_CHIP_Y, BAY_ZC - 1.5, TOF_XW + 3.2, TOF_PCB_Y, BAY_ZC + 1.5)
    return [cam, tof, chip]


# ---------- arms (local: x 0 = head side -> outward, y 0 = front -> back, z 0 = bottom) ----------
def arm_T(m, side):  # side +1 = left arm (+X), -1 = right arm (mirror)
    w = m.rotate([0, 0, -SPLAY]).translate([X_P, Y_P, Z0])
    return w if side > 0 else w.mirror([1, 0, 0])


def arm_T_inv(m, side):
    if side < 0:
        m = m.mirror([1, 0, 0])
    return m.translate([-X_P, -Y_P, -Z0]).rotate([0, 0, SPLAY])


def arm(side):
    """Hollow arm; the front 'knuckle' blends from the brow end into the housing and carries the wire channel."""
    t, h, L = HOUSE_T, HOUSE_H, HOUSE_L
    solid = box(0, 0, 0, t, L, h)
    solid += (box(0, L - 0.5, 0, 5, L, h) + box(0, L + TAPER - 0.5, 0, 5, L + TAPER, 10)).hull()  # taper
    solid += box(0, L + TAPER - 0.5, 0, 5, ARM_LEN, 10)                                          # tail
    cuts = box(1.5, 1.5, 1.5, t + 1, L - 1.5, h - 1.5)            # cavity, open on the outer face for the lid
    cuts += box(-1, ARM_LEN - 9, 1.5, 6, ARM_LEN - 6, 8.5)        # strap slot
    if side < 0:   # ESP32 arm: USB-C notch at the back (ports face outward)
        cuts += box(5, L - 2.5, 4.5, t + 1, L + 1, h - 4.5)
    else:          # power arm: IP5310 charge port at the back, switch underneath
        cuts += box(5, L - 2.5, 11, t + 1, L + 1, 21)
        cuts += box(3, 46, -1, 3 + SWITCH_SLOT[1], 46 + SWITCH_SLOT[0], 1.6)
    s = side
    brow_end = bend(box(min(s * (HALF_ARC + .05), s * (HALF_ARC + .6)), 0, BROW_Z[0],
                        max(s * (HALF_ARC + .05), s * (HALF_ARC + .6)), BROW_D, BROW_Z[1]), 0.3)
    knuckle = (brow_end + arm_T(box(0, 0, 0, t, 0.5, h), side)).hull()
    tun_end = bend(box(min(s * (HALF_ARC - .5), s * (HALF_ARC + .6)), TUN[0], TUN[2],
                       max(s * (HALF_ARC - .5), s * (HALF_ARC + .6)), TUN[1], TUN[3]), 0.3)
    zc = (TUN[2] + TUN[3]) / 2 - Z0
    channel = (tun_end + arm_T(box(1.5, 1.0, zc - 8, 4.5, 2.0, zc + 8), side)).hull()
    return arm_T(solid, side) + knuckle - arm_T(cuts, side) - channel


def arm_lid(side):
    t, h, L = HOUSE_T, HOUSE_H, HOUSE_L
    y0, y1, z0, z1 = 1.5 + FIT, L - 1.5 - FIT, 1.5 + FIT, h - 1.5 - FIT
    m = box(12, y0, z0, t, y1, z1)
    m += box(11, y0, z0, 12, y1, z1) - box(10, y0 + 1, z0 + 1, 12.5, y1 - 1, z1 - 1)   # press-fit ring
    if side < 0:   # vents over the ESP32 module
        for y in (6, 10, 14, 18, 22):
            m -= box(10, y, h / 2 - 7, t + 1, y + 2, h / 2 + 7)
    return m


def esp32_dummy():  # arm-local; components face outward, USB-C at the back
    L = HOUSE_L
    ports = [box(7.1, L - 1.5 - 7.5, zc - 4.5, 10.3, L - 1.5 - 0.05, zc + 4.5) for zc in (HOUSE_H / 2 - 6, HOUSE_H / 2 + 6)]
    return [box(5.5, 2.5, 2.0, 7.1, 2.5 + BOARD_L, 2.0 + BOARD_W),                    # PCB
            box(7.1, 2.5, HOUSE_H / 2 - 9, 10.3, 28.0, HOUSE_H / 2 + 9),              # module (antenna at front)
            box(2.0, 4.0, 2.5, 5.5, BOARD_L, 5.0), box(2.0, 4.0, BOARD_W - 1.0, 5.5, BOARD_L, BOARD_W + 1.5)] + ports


def power_dummies():  # arm-local
    bl, bh, bt = BATT
    il, ih, it = IP5310
    batt = box(1.75, 1.7, HOUSE_H / 2 - bh / 2, 1.75 + bt, 1.7 + bl, HOUSE_H / 2 + bh / 2)
    ip = box(5.9, 1.8 + bl, HOUSE_H / 2 - ih / 2, 5.9 + it, 1.8 + bl + il, HOUSE_H / 2 + ih / 2)
    switch = box(3, 46, 1.6, 7, 54.5, 5.1)
    return [batt, ip, switch]


def assembly(exploded=False):
    e = (lambda v: v) if exploded else (lambda v: [0, 0, 0])
    parts = [brow(), lens().translate(e([0, -25, -30])), bay_cover().translate(e([0, 35, 0]))]
    for s in (-1, 1):
        parts += [arm(s).translate(e([s * 40, 15, 0])),
                  arm_T(arm_lid(s), s).translate(e([s * 75, 15, 0]))]
    if exploded:
        parts += [d.translate([0, 18, 0]) for d in bay_dummies()]
        parts += [arm_T(d, -1).translate([-58, 15, 0]) for d in esp32_dummy()]
        parts += [arm_T(d, 1).translate([58, 15, 0]) for d in power_dummies()]
    return parts


def fit_check():
    def clear(name, a, b):
        v = (a ^ b).volume()
        assert v < 1e-3, f"{name} collide by {v:.2f} mm3"

    b, cov, ln = brow(), bay_cover(), lens()
    for i, d in enumerate(bay_dummies()):
        clear(f"bay part {i}/brow", d, b); clear(f"bay part {i}/cover", d, cov)
    clear("camera/ToF", bay_dummies()[0], bay_dummies()[1] + bay_dummies()[2])
    clear("camera line of sight", box(CAM_XW - 3, -3, BAY_ZC - 3, CAM_XW + 3, CAM_Y, BAY_ZC + 3), b)
    clear("ToF line of sight", box(TOF_XW - 3.2, -3, BAY_ZC - 1.5, TOF_XW + 3.2, TOF_CHIP_Y, BAY_ZC + 1.5), b)
    clear("lens/brow", ln, b); clear("cover/brow", cov, b)
    for s, dummies in ((-1, esp32_dummy()), (1, power_dummies())):
        a, lid = arm(s), arm_T(arm_lid(s), s)
        clear(f"arm {s}/brow", a, b); clear(f"arm {s}/lens", a, ln); clear(f"lid {s}/arm", lid, a)
        for i, d in enumerate(dummies):
            clear(f"arm {s} part {i}/arm", arm_T(d, s), a); clear(f"arm {s} part {i}/lid", arm_T(d, s), lid)
    L = HOUSE_L
    plug = union([box(5.2, L - 1.5, zc - 4.5, 12.2, L + 8, zc + 4.5) for zc in (HOUSE_H / 2 - 6, HOUSE_H / 2 + 6)])
    clear("ESP32 USB-C plug path", arm_T(plug, -1), arm(-1))
    clear("charge plug path", arm_T(box(5.15, L - 1.5, 11.75, 11.65, L + 8, 20.25), 1), arm(1))
    clear("switch knob path", arm_T(box(4, 47.5, -2, 6, 53, 1.6), 1), arm(1))


PARTS = {   # printable, in print orientation
    "brow_frame": lambda: on_bed(brow().rotate([180, 0, 0])),                          # upside down, top on bed
    "bay_cover": lambda: on_bed(bay_cover()),                                          # stands on its edge
    "arm_right_esp32": lambda: on_bed(arm_T_inv(arm(-1), -1).mirror([1, 0, 0])),       # stands on its bottom edge
    "arm_left_power": lambda: on_bed(arm_T_inv(arm(1), 1)),
    "lid_right": lambda: on_bed(arm_lid(-1).mirror([1, 0, 0]).rotate([0, -90, 0])),    # outer face down
    "lid_left": lambda: on_bed(arm_lid(1).rotate([0, 90, 0])),
    "lens_cut_template": lambda: on_bed(Manifold.extrude(lens_2d(), 1.0)),             # trace onto 1.5 mm sheet
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
    for name, exploded in (("shield_assembly", False), ("shield_exploded", True)):
        parts = assembly(exploded)
        write_stl(parts, out / f"{name}.stl")
        lo = np.min([p.bounding_box()[:3] for p in parts], axis=0)
        hi = np.max([p.bounding_box()[3:] for p in parts], axis=0)
        print(f"{name:18s} {hi[0]-lo[0]:6.1f} x {hi[1]-lo[1]:6.1f} x {hi[2]-lo[2]:5.1f} mm  (preview only)")
