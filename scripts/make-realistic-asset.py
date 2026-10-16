#!/usr/bin/env python3
"""Build the controller artwork used by the app from art/controller-realistic-source.png.

    python3 scripts/make-realistic-asset.py          (needs numpy, scipy and Pillow)

Steps:
  1. cut the controller out of its flat dark background (the background is perfectly flat,
     the controller's surface never is), remove the soft drop shadow around the grips,
     and feather the edge;
  2. remove the image's baked yellow light bar ring tint (padlink draws the live colour) and erase
     its Mute button (padlink draws PS there and Mute below it);
  3. put the labels that float outside the controller into a separate white+alpha layer,
     which the app tints with the theme's text colour, minus the static L2/R2 pills
     (the app draws live ones);
  4. crop both layers to the content and print the crop offset for padview.py (R_CROP).
"""
import json
import pathlib
import sys

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage as ndi

ROOT = pathlib.Path(__file__).resolve().parent.parent
src = ROOT / "art" / "controller-realistic-source.png"
out_body = ROOT / "padlink" / "gui" / "assets" / "controller-realistic.png"
out_labels = ROOT / "padlink" / "gui" / "assets" / "controller-realistic-labels.png"
rgb = np.asarray(Image.open(src).convert("RGB")).astype(float)
h, w, _ = rgb.shape
bg = np.median(np.concatenate([rgb[:8].reshape(-1, 3), rgb[-8:].reshape(-1, 3)]), axis=0)
d = np.abs(rgb - bg).max(axis=2)
lum = rgb.mean(axis=2)

# label areas that float on the background (pills + bottom stick labels): not part of the body
LABEL_BOXES = [(295, 28, 505, 112), (1160, 28, 1372, 112), (530, 652, 740, 765), (925, 652, 1140, 765)]
d_body = d.copy()
for x0, y0, x1, y1 in LABEL_BOXES:
    d_body[y0:y1, x0:x1] = 0

# 1. coarse body: the background is perfectly flat, the controller's surface never is
smooth = ndi.uniform_filter(d_body, 15)
m = smooth > 3.0
lab, n = ndi.label(m)
body = lab == (np.argmax(ndi.sum(m, lab, range(1, n + 1))) + 1)
body = ndi.binary_fill_holes(body)

# 2. refine the edge pixel by pixel inside a band around the coarse boundary
band = ndi.binary_dilation(body, iterations=9) & ~ndi.binary_erosion(body, iterations=9)
fine = ndi.uniform_filter(d_body, 5) > 2.6
refined = (body & ~band) | (band & fine)
refined = ndi.binary_opening(refined, iterations=2)
lab, n = ndi.label(refined)
refined = lab == (np.argmax(ndi.sum(refined, lab, range(1, n + 1))) + 1)
refined = ndi.binary_fill_holes(refined)
refined = ndi.median_filter(refined.astype(np.uint8), size=7).astype(bool)

# 3. remove the soft drop shadow around the grips: flood from the image border through pixels that
#    are background-flat or darker than the background; the controller's bright rim stops the flood
lum_s = ndi.uniform_filter(lum, 5) - lum[:8].mean()
passable = (lum_s < 1.6) & (ndi.uniform_filter(d_body, 5) < 14)
for x0, y0, x1, y1 in LABEL_BOXES:
    passable[y0:y1, x0:x1] = True
seed = np.zeros_like(passable)
seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
lab, n = ndi.label(passable)
outside_ids = np.unique(lab[seed & passable])
flooded = np.isin(lab, outside_ids[outside_ids > 0])
refined &= ~flooded
lab, n = ndi.label(refined)
refined = lab == (np.argmax(ndi.sum(refined, lab, range(1, n + 1))) + 1)
refined = ndi.binary_fill_holes(refined)
refined = ndi.median_filter(refined.astype(np.uint8), size=7).astype(bool)

# the stick rings are circles that reach past the controller's lower edge: keep them whole
yy, xx = np.mgrid[0:h, 0:w]
for scx, scy in ((635, 545), (1032, 545)):
    refined |= (xx - scx) ** 2 + (yy - scy) ** 2 <= 90 ** 2

alpha = np.asarray(Image.fromarray((refined * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.3)))
# 2. remove the baked yellow ring tint around the touch pad
x0, x1, y0, y1 = 570, 1100, 150, 395
reg = rgb[y0:y1, x0:x1]
warm = (reg[..., 0] > reg[..., 2] + 5) & (reg[..., 1] >= reg[..., 2] + 2)
gray = np.clip(reg[..., 2], 12, 255)
for c, off in ((0, 0), (1, 1), (2, 4)):
    reg[..., c][warm] = np.clip(gray[warm] + off, 0, 255)
rgb[y0:y1, x0:x1] = reg
# 2b. erase the image's Mute button (button, leader line and label): padlink draws PS here and
#     Mute below it. Only those pixels are replaced (the right stick's ring is protected), filled by
#     diffusing the surrounding surface into the hole.
yy2, xx2 = np.mgrid[0:h, 0:w]
hole = np.zeros((h, w), bool)
hole[436:496, 786:878] = True                  # button + its shadow
hole[442:474, 870:958] = True                  # leader line + "Mute"
hole &= (xx2 - 1032) ** 2 + (yy2 - 545) ** 2 > 110 ** 2
ys_, xs_ = np.nonzero(hole)
y0_, y1_, x0_, x1_ = ys_.min() - 2, ys_.max() + 3, xs_.min() - 2, xs_.max() + 3
sub = rgb[y0_:y1_, x0_:x1_].copy()
hm = hole[y0_:y1_, x0_:x1_]
sub[hm] = sub[~hm].mean(axis=0)
for _ in range(1500):                          # Laplace fill: average of neighbours inside the hole
    avg = (np.roll(sub, 1, 0) + np.roll(sub, -1, 0) + np.roll(sub, 1, 1) + np.roll(sub, -1, 1)) / 4
    sub[hm] = avg[hm]
rgb[y0_:y1_, x0_:x1_] = sub
body_rgba = np.dstack([rgb.astype(np.uint8), alpha])

# 3. labels outside the body -> alpha from brightness
outside = ~ndi.binary_dilation(refined, iterations=2)
la = (np.clip((lum - bg.mean() - 45) / (200 - bg.mean()), 0, 1) * outside * 255).astype(np.uint8)   # text/lines only, not faint glows
for x0, y0, x1, y1 in ((295, 28, 505, 90), (1160, 28, 1372, 90)):      # static L2/R2 pills
    la[y0:y1, x0:x1] = 0
labels_rgba = np.dstack([np.full((h, w), 255, np.uint8)] * 3 + [la])

# 4. crop to content (+ a margin for the live pills' glow)
used = (alpha > 0) | (la > 0)
ys, xs = np.nonzero(used)
pad = 24
cx0, cy0 = max(0, xs.min() - pad), max(0, min(ys.min() - pad, 28))   # keep room for the live L2/R2 pills
cx1, cy1 = min(w, xs.max() + pad), min(h, ys.max() + pad)
Image.fromarray(body_rgba[cy0:cy1, cx0:cx1], "RGBA").save(out_body, optimize=True)
Image.fromarray(labels_rgba[cy0:cy1, cx0:cx1], "RGBA").save(out_labels, optimize=True)
print(json.dumps({"R_CROP": [int(cx0), int(cy0)], "size": [int(cx1 - cx0), int(cy1 - cy0)]}))

