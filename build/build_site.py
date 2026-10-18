#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kouta Deals — shop page builder.

Builds site/index.html plus site/img/*.jpg from:
  sheet.tsv   product data exported from the Google Sheet
  photos/     the permanent photo bank

Photos are written as SEPARATE files (not embedded), so they can be served at
high resolution and the phone only downloads what is on screen.

Usage:  python3 build_shop.py [outdir]      (default: site)
"""
import os, re, sys, html, glob, shutil, collections
from urllib.parse import quote
from PIL import Image, ImageOps, ImageDraw, ImageFont

import json as _json, urllib.request as _url

HERE   = os.path.dirname(os.path.abspath(__file__))
SHEET  = os.path.join(HERE, "sheet.tsv")
PHOTOS = os.environ.get("KD_PHOTOS") or os.path.join(HERE, "photos")
OUT    = os.path.abspath(os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "."))
IMGDIR = os.path.join(OUT, "img")
MANIFEST = os.path.join(HERE, "manifest.json")

# where the sheet is read from when this runs on GitHub
SHEET_URL    = os.environ.get("KD_SHEET_URL", "")
SHEET_SECRET = os.environ.get("KD_SHEET_SECRET", "")

def load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            m = _json.load(f)
        return m.get("products", {}), m.get("tiles", {})
    except Exception:
        return {}, {}

MAN_PRODUCTS, MAN_TILES = load_manifest()

def fetch_rows():
    """live sheet when running on GitHub; the local copy when running here"""
    if not SHEET_URL:
        return None
    body = _json.dumps({"secret": SHEET_SECRET, "action": "read"}).encode()
    req  = _url.Request(SHEET_URL, data=body, headers={"Content-Type": "application/json"})
    with _url.urlopen(req, timeout=120) as r:
        data = _json.loads(r.read().decode())
    if not data.get("ok"):
        raise SystemExit("sheet read failed: %s" % data.get("error"))
    head = ["category","marque","stock","style","tailles","saison","matiere",
            "c1","c2","c3","c4","ancien","prix","cur","photos","descr"]
    out = []
    for row in data["rows"]:
        row = list(row) + [""] * (len(head) - len(row))
        out.append(dict(zip(head, row)))
    return out

SOLD_MAX = 20                  # sold pieces kept on the page, per category

BIG_W,  BIG_Q  = 1000, 82      # main photo / what a thumbnail swaps in
THUMB_W, THUMB_Q = 220, 78     # the small strip under the photo
TILE_W, TILE_Q = 700, 80       # category menu cover

WA   = "212661793236"          # WhatsApp number the order is sent to
WA1  = "34611392655"           # second number, link form
WA2  = "+34 611 392 655"
WA3  = "+212 661 793 236"
SITE = "https://koutadeals.com"
SHARE_TXT = SITE
SHARE_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
              'aria-hidden="true"><circle cx="18" cy="5" r="2.6"/>'
              '<circle cx="6" cy="12" r="2.6"/><circle cx="18" cy="19" r="2.6"/>'
              '<line x1="8.3" y1="10.8" x2="15.7" y2="6.2"/>'
              '<line x1="8.3" y1="13.2" x2="15.7" y2="17.8"/></svg>')
SHARE_IMG = "share-1.jpg"          # the picture WhatsApp shows for the link
SHARE_V   = "1"                  # bump when the picture changes (forces a refresh)
SHARE_PHOTOS = [                 # the five products on that picture, left to right
    "Pyjama Charliniz_Undiz_1.jpg",
    "Collants transparents Amour_Calzedonia_1.jpg",
    "Gloss repulpant à la teinte 490 Berry Jolie_L’Oreal_1.jpg",
    "Torchon de cuisine Gourmandise_Douceur d'intérieur_1.jpg",
    "Pack chaussettes_Women_s Secret_1.jpg",
]
COVERS = os.path.join(HERE, "covers.tsv")   # optional: category <TAB> style name

# photos confirmed to be phone screenshots rather than product shots
SKIP_PHOTOS = {
    "Holiday Premi\u00e8re Iconic Masterpiece Blush_Kiko.png",
    "Tongs_Havaianas.PNG",
    "Tongs_Havaianas (1).PNG",
    "Tongs_Havaianas (2).PNG",
    "Tongs_Havaianas_1.PNG",
}
ALL_SEASONS = "toutes saisons"
SEASONS = [("été", "Été"), ("demi-saison", "Demi-saison"), ("hiver", "Hiver")]

# ---------------------------------------------------------------- helpers
def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

def srank(v):
    v = (v or "").strip().lower()
    if v == ALL_SEASONS:
        return -1
    for i, (k, _) in enumerate(SEASONS):
        if v == k:
            return i
    return len(SEASONS)

def sizes_of(cell):
    """Whatever Kaoutar typed IS the size, shown exactly as typed.
    Only a COMMA creates several choices.  'S, M, L' -> three options;
    '37-42', 'M/L', '38', 'Taille unique', '130 x 160 cm' -> one option."""
    cell = (cell or "").strip()
    if not cell:
        return []                       # empty -> no size, quantity only
    parts = [p.strip() for p in cell.split(",") if p.strip()]
    seen, out = set(), []
    for p in parts:
        k = p.casefold()
        if k not in seen:
            seen.add(k)
            # 90b -> 90B, m -> M, xl -> XL;  "Taille unique" is left alone
            out.append(p.upper() if (" " not in p and len(p) <= 4) else p)
    return out

def e(s):
    return html.escape(str(s if s is not None else ""), quote=True)

# ---------------------------------------------------------------- read data
def read_covers():
    """category -> (cover style name, tagline), from the Sheet's Manage categories tab."""
    cov, tag = {}, {}
    if not os.path.exists(COVERS):
        return cov, tag
    with open(COVERS, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if not parts or not parts[0].strip():
                continue
            cat = parts[0].strip()
            if len(parts) >= 2 and parts[1].strip():
                cov[cat] = parts[1].strip()
            if len(parts) >= 3 and parts[2].strip():
                tag[cat] = parts[2].strip()
    return cov, tag

def read_sheet():
    rows = []
    with open(SHEET, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            if not line.strip():
                continue
            cells = line.rstrip("\n").split("\t")
            cells += [""] * (len(head) - len(cells))
            rows.append(dict(zip(head, cells)))
    return rows

def index_photos():
    """stem(normalised) -> list of file paths, hero first."""
    files = []
    for ext in ("jpg", "jpeg", "png", "webp", "avif", "JPG", "JPEG", "PNG", "WEBP"):
        files += glob.glob(os.path.join(PHOTOS, "*." + ext))
    return sorted(set(files))

STYLES_N  = []        # every product name, normalised, longest first
STYLE_DUP = set()     # names used by more than one product

def old_price_html(anc, prix, cur):
    """left: the old price crossed out.  right: the green badge.  ("", "") when
    there is no real discount"""
    def num(s):
        s = re.sub(r"[^0-9.,]", "", s or "").replace(",", ".")
        try:
            return float(s)
        except Exception:
            return 0.0
    a, p = num(anc), num(prix)
    if not anc or a <= p or p <= 0:
        return "", ""
    left = "<s>%s %s</s>" % (e(anc), e(cur))
    off  = int(round((1 - p / a) * 100))
    right = ("<em class=\"off\">-%d&nbsp;%%</em>" % off) if off >= 1 else ""
    return left, right

def match_photos(style, brand, files):
    """The photos of ONE product.  The longest product name wins, and when
    two products share a name the brand must appear in the file name."""
    sn = norm(style)
    bn = norm(brand)
    if not sn:
        return []
    longer = [s for s in STYLES_N if len(s) > len(sn) and s.startswith(sn)]
    hits = []
    for f in files:
        stem = norm(os.path.splitext(os.path.basename(f))[0])
        if not stem.startswith(sn):
            continue
        if any(stem.startswith(s) for s in longer):
            continue                 # it belongs to a longer product name
        if sn in STYLE_DUP and not (bn and stem[len(sn):].startswith(bn)):
            continue                 # two products share this name
        hits.append(f)
    if hits:
        def is_hero(p):
            stem = os.path.splitext(os.path.basename(p))[0]
            return stem.endswith("_1")
        hits.sort(key=lambda p: (not is_hero(p), os.path.basename(p).lower()))
        return hits
    return []

# ---------------------------------------------------------------- images
def slug(s, n=60):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "")).strip("-").lower()
    return (s[:n] or "x").strip("-")

_written = {}
def emit(src, width, quality, suffix):
    """Resize src into IMGDIR, return the relative href. Cached per (src,suffix)."""
    key = (src, suffix)
    if key in _written:
        return _written[key]
    base = slug(os.path.splitext(os.path.basename(src))[0])
    name = "%s%s.jpg" % (base, suffix)
    i = 2
    while os.path.exists(os.path.join(IMGDIR, name)) and _written.get((name,)) != src:
        name = "%s-%d%s.jpg" % (base, i, suffix); i += 1
    _written[(name,)] = src
    href_existing = "img/" + name
    if os.path.exists(os.path.join(IMGDIR, name)) and not os.path.exists(src):
        _written[key] = href_existing
        return href_existing
    im = Image.open(src)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        # transparent source (webp/png): flatten onto white, never black
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if im.width > width:
        h = round(im.height * width / im.width)
        im = im.resize((width, h), Image.LANCZOS)
    im.save(os.path.join(IMGDIR, name), "JPEG",
            quality=quality, optimize=True, progressive=True)
    href = "img/" + name
    _written[key] = href
    return href

# ---------------------------------------------------------------- build
# ---------------------------------------------------------------- share card
CAL = "/usr/share/fonts/truetype/crosextra/Caladea-%s.ttf"

def _cal(style, size):
    try:
        return ImageFont.truetype(CAL % style, size)
    except OSError:
        return ImageFont.load_default(size)

def _fill(path, w, h):
    """cover-crop a photo to exactly w x h"""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im = im.convert("RGB")
    sw, sh = im.size
    sc = max(w / float(sw), h / float(sh))
    im = im.resize((max(1, int(sw * sc + .5)), max(1, int(sh * sc + .5))), Image.LANCZOS)
    nw, nh = im.size
    return im.crop(((nw - w) // 2, (nh - h) // 2, (nw - w) // 2 + w, (nh - h) // 2 + h))

def _centred(d, cx, y, txt, font, col, track=0):
    if track:
        ws = [d.textlength(c, font=font) for c in txt]
        x = cx - (sum(ws) + track * (len(txt) - 1)) / 2.0
        for c, cw in zip(txt, ws):
            d.text((x, y), c, font=font, fill=col)
            x += cw + track
    else:
        d.text((cx - d.textlength(txt, font=font) / 2.0, y), txt, font=font, fill=col)

def write_share_card(outdir):
    """The picture WhatsApp (and Facebook, Messenger...) shows for the link."""
    W, H = 1200, 630
    BG, GOLD, DEEP, MUTED = (251, 246, 241), (176, 141, 87), (58, 47, 42), (138, 120, 109)
    im = Image.new("RGB", (W, H), BG)
    d  = ImageDraw.Draw(im)
    cx = W // 2
    _centred(d, cx, 86, u"KOUTA DEALS", _cal("Regular", 62), DEEP, track=8)
    d.line([(cx - 150, 182), (cx + 150, 182)], fill=GOLD, width=2)
    _centred(d, cx, 206, u"Iconic · Chic · Timeless", _cal("Italic", 27), GOLD)

    pics = [os.path.join(PHOTOS, n) for n in SHARE_PHOTOS]
    pics = [p for p in pics if os.path.exists(p)]
    if len(pics) < 5:                      # a photo was renamed: fall back to the bank
        have = set(pics)
        for p in sorted(glob.glob(os.path.join(PHOTOS, "*"))):
            if len(pics) >= 5:
                break
            if (p not in have and os.path.isfile(p)
                    and os.path.basename(p) not in SKIP_PHOTOS):
                pics.append(p)
    pics = pics[:5]
    gap  = 10
    sw   = (W - 80 - gap * (len(pics) - 1)) // max(1, len(pics))
    y    = 286
    for i, p in enumerate(pics):
        im.paste(_fill(p, sw, sw), (40 + i * (sw + gap), y))
    _centred(d, cx, y + sw + 28, u"koutadeals.com", _cal("Regular", 24), MUTED, track=1)

    dst = os.path.join(outdir, SHARE_IMG)
    im.save(dst, "JPEG", quality=88, optimize=True)
    return dst



TILES_USED = {}

def main():
    rows   = fetch_rows() or read_sheet()
    files  = index_photos()
    covers, taglines = read_covers()

    os.makedirs(IMGDIR, exist_ok=True)   # never wipe: published images stay

    items, problems = [], []

    # product names, so one product cannot take another product photos
    _real = {}
    for _r in rows:
        _s = (_r.get("style") or "").strip()
        if _s:
            _real.setdefault(norm(_s), _s)
    _count = collections.Counter(norm((_r.get("style") or "").strip()) for _r in rows)
    _count.pop("", None)
    STYLES_N[:] = sorted(_real.keys(), key=len, reverse=True)
    STYLE_DUP.clear()
    STYLE_DUP.update(n for n, c in _count.items() if c > 1)
    for n, c in sorted(_count.items()):
        if c > 1:
            problems.append("same product name used by %d products: %s" % (c, _real[n]))
    for n in STYLES_N:
        for s in STYLES_N:
            if s != n and s.startswith(n):
                problems.append("the name %s is the beginning of %s" % (_real[n], _real[s]))
                break

    for r in rows:
        cat   = (r.get("category") or "").strip()
        stock = (r.get("stock") or "").strip()
        style = (r.get("style") or "").strip()
        brand = (r.get("marque") or "").strip()
        if not style:
            continue
        if not cat:
            problems.append("no category: %s" % style);            continue
        sold    = (stock == "0")
        stock_n = "" if sold else (stock if stock.isdigit() else "")
        pics = match_photos(style, brand, files)
        # skip photos confirmed by eye to be phone screenshots; only flag the rest
        keep = []
        for pth in pics:
            base = os.path.basename(pth)
            if base in SKIP_PHOTOS:
                problems.append("phone screenshot skipped: %s" % base)
                continue
            try:
                w, h = Image.open(pth).size
                if h and w and h / float(w) > 2.1:
                    problems.append("very tall photo, check it is not a screenshot: %s" % base)
            except Exception:
                pass
            keep.append(pth)
        pics = keep or pics
        ready = MAN_PRODUCTS.get(style + "|" + brand)    # already published images
        if not pics and not ready:
            problems.append("NO PHOTO: %s" % style);                continue
        if pics:
            stem = os.path.splitext(os.path.basename(pics[0]))[0]
            if not stem.endswith("_1"):
                problems.append("no _1 hero photo: %s" % style)
        prix = (r.get("prix") or "").strip()
        cur  = (r.get("cur") or "").strip() or "MAD"   # blank cell -> MAD
        old_html, off_html = old_price_html((r.get("ancien") or "").strip(), prix, cur)
        tal   = (r.get("tailles") or "").strip()
        sizes = sizes_of(tal)
        note  = "" if (sizes or tal.lower() in ("", "unique", "taille unique", "u")) else tal
        items.append(dict(
            cat=cat, brand=brand, name=style, note=note, stock=stock_n,
            sizes=sizes,
            season=(r.get("saison") or "").strip(),
            price=prix, label=("%s %s" % (prix, cur) if prix else ""), old=old_html, off=off_html,
            descr=(r.get("descr") or "").strip()[:400],
            sold=sold, rowno=len(items),
            pics=pics, ready=ready))

    # category order: biggest first, first-seen as tie-break
    cnt  = collections.Counter(i["cat"] for i in items if not i["sold"])
    seen = []
    for i in items:
        if i["cat"] not in seen:
            seen.append(i["cat"])
    order = {c: n for n, c in enumerate(seen)}
    cats  = sorted(set(i["cat"] for i in items), key=lambda c: (-cnt[c], order[c]))

    # season order inside a category; sold ones always after, newest first
    items.sort(key=lambda i: (cats.index(i["cat"]), i["sold"],
                              -i["rowno"] if i["sold"] else srank(i["season"]),
                              i["name"].lower()))

    # only the last SOLD_MAX sold pieces of each category stay on the page.
    # nothing is deleted anywhere: the sheet row and the photos are untouched,
    # the card is simply not printed.
    keep, nsold = [], collections.Counter()
    for i in items:
        if i["sold"]:
            nsold[i["cat"]] += 1
            if nsold[i["cat"]] > SOLD_MAX:
                continue
        keep.append(i)
    dropped = len(items) - len(keep)
    if dropped:
        problems.append("%d older sold pieces are kept in the sheet but not shown" % dropped)
    items = keep

    # ---- category tiles
    tiles = []
    for n, c in enumerate(cats):
        want  = covers.get(c, "")
        first, pic = None, None
        if want:
            w = norm(want)
            # 1. an exact photo file
            for i in items:
                if i["cat"] != c:
                    continue
                for pth in i["pics"]:
                    if norm(os.path.splitext(os.path.basename(pth))[0]) == w:
                        first, pic = i, pth
                        break
                if pic:
                    break
            # 2. otherwise a style name (also matches "style_brand_1" spellings)
            if first is None:
                cand = [i for i in items
                        if i["cat"] == c and w.startswith(norm(i["name"]))]
                if cand:
                    first = max(cand, key=lambda i: len(norm(i["name"])))
            if first is None:
                problems.append("cover photo not found in %s: %r" % (c, want))
        if first is None:
            first = next((i for i in items if i["cat"] == c and not i["sold"]),
                         next(i for i in items if i["cat"] == c))
        if pic or first["pics"]:
            href = emit(pic or first["pics"][0], TILE_W, TILE_Q, "-tile")
        else:
            href = MAN_TILES.get(c) or (first["ready"]["big"][0] if first.get("ready") else "")
        TILES_USED[c] = href
        num   = cnt[c]
        sub = taglines.get(c, "")
        tiles.append(
            '<a class="tile" href="#/c/%d">\n'
            '  <span class="tshot"><img src="%s" alt="" loading="lazy" width="700" height="525"></span>\n'
            '  <span class="tmeta"><span class="tname">%s</span>'
            '<span class="tnum">%s</span></span>\n'
            '  <span class="tgo" aria-hidden="true">&rsaquo;</span>\n'
            '</a>' % (n, href, e(c[:1].upper() + c[1:]), e(sub)))

    # ---- a short address for every product, used by the share link
    taken = {}
    for it in items:
        s = slug(it["name"] + "-" + it["brand"])
        if s in taken:
            taken[s] += 1
            s = "%s-%d" % (s, taken[s])
        else:
            taken[s] = 1
        it["slug"] = s

    # ---- product cards
    cards = []
    for it in items:
        rank = srank(it["season"])
        sea  = "*" if rank == -1 else (SEASONS[rank][1] if 0 <= rank < len(SEASONS) else "")
        # already published photos first, then any new originals, never losing either
        rd    = it["ready"] or {}
        rbig  = list(rd.get("big") or [])
        rthm  = list(rd.get("thumb") or []) or rbig
        pairs = [(b, rthm[n] if n < len(rthm) else b) for n, b in enumerate(rbig)]
        have  = set(b for b, _ in pairs)
        for p in it["pics"]:
            b = emit(p, BIG_W, BIG_Q, "")
            t = emit(p, THUMB_W, THUMB_Q, "-t")
            if b not in have:
                pairs.append((b, t)); have.add(b)
        big = [b for b, _ in pairs]
        thm = [t for _, t in pairs]
        it["_big"], it["_thm"] = big, thm

        gal = ""
        if len(big) > 1:
            for n, (b, t) in enumerate(zip(big, thm)):
                gal += ('<button class="th%s" data-src="%s" aria-label="Photo %d">'
                        '<img src="%s" alt="" loading="lazy" width="56" height="74">'
                        '</button>' % (" on" if n == 0 else "", b, n + 1, t))

        qty = ('<div class="qty%s"><button type="button" class="qb" data-dec aria-label="Moins">&minus;</button>'
               '<span data-qty>1</span>'
               '<button type="button" class="qb" data-inc aria-label="Plus">+</button></div>')
        if it["sizes"]:
            opts = "".join('<option value="%s">%s</option>' % (e(s), e(s)) for s in it["sizes"])
            sz = ('<div class="opts">'
                  '<label class="sz"><span>%s</span><select data-size>%s</select></label>'
                  '%s</div>'
                  % ("Taille" if len(it["sizes"]) == 1 else "Tailles", opts, qty % ""))
        else:
            lab = e(it["note"]) if it["note"] else "Quantit&eacute;"
            sz = ('<div class="opts"><label class="sz solo"><span>%s</span></label>%s</div>'
                  % (lab, qty % " solo"))

        dsc   = it["descr"]
        dattr = (' data-descr="%s"' % e(dsc).replace("\n", "&#10;")) if dsc else ""
        more  = '\n    <button class="more" type="button">En savoir plus</button>' if dsc else ""

        sold  = it["sold"]
        scls  = " sold" if sold else ""
        sattr = ' data-sold="1"' if sold else ""
        stick = '\n    <span class="soldtag">Vendu</span>' if sold else ""
        tail  = ('<p class="soldline">Vendu</p>' if sold else
                 '<button class="pick" aria-pressed="false"><span>Ajouter</span></button>')

        cards.append(
            '<article class="card%s" data-cat="%s" data-season="%s"\n'
            '         data-name="%s" data-brand="%s" data-stock="%s"\n'
            '         data-price="%s" data-label="%s" data-slug="%s"%s%s>\n'
            '  <div class="shot"><img src="%s" alt="%s" loading="lazy" width="1000" height="1333">%s\n'
            '    <button class="shr" type="button" aria-label="Partager cet article">%s</button>\n'
            '    <span class="tick" aria-hidden="true">&#10003;</span></div>\n'
            '  <div class="gallery">%s</div>\n'
            '  <div class="meta">\n'
            '    <p class="brand">%s</p>\n'
            '    <h3>%s</h3>%s\n'
            '    %s\n'
            '    <p class="smax" data-max hidden>Stock limit&eacute;&nbsp;: quantit&eacute; maximale atteinte.</p>\n'
            '    <p class="price"><span class="pL">%s</span>'
            '<b>%s</b><span class="pR">%s</span></p>\n'
            '    %s\n'
            '  </div>\n'
            '</article>'
            % (scls, e(it["cat"]), e(sea), e(it["name"]), e(it["brand"]), e(it["stock"]),
               e(it["price"]), e(it["label"]), e(it["slug"]), dattr, sattr,
               big[0], e(it["name"]), stick, SHARE_ICON,
               gal, e(it["brand"]), e(it["name"]), more, ("" if sold else sz),
               it["old"], e(it["label"]), it["off"], tail))

    page = TEMPLATE.replace("{{TILES}}", "\n".join(tiles)) \
                   .replace("{{CARDS}}", "\n".join(cards)) \
                   .replace("{{CATS}}",  ", ".join(repr(c[:1].upper() + c[1:]) for c in cats)) \
                   .replace("{{RAW}}",   ", ".join(repr(c) for c in cats)) \
                   .replace("{{WA1R}}",  WA1) \
                   .replace("{{WA}}",    WA) \
                   .replace("{{WA2}}",   WA2) \
                   .replace("{{WA3}}",   WA3) \
                   .replace("{{SHARE}}", quote(SHARE_TXT, safe="")) \
                   .replace("{{OGIMG}}", SITE + "/" + SHARE_IMG) \
                   .replace("{{SITE}}",  SITE)

    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    open(os.path.join(OUT, ".nojekyll"), "w").close()

    # ---- one small page per product, so a shared link carries its own photo
    pdir = os.path.join(OUT, "p")
    shutil.rmtree(pdir, ignore_errors=True)
    for it in items:
        if not it.get("slug") or not it["_big"]:
            continue
        title = it["name"] + ((" \u2014 " + it["brand"]) if it["brand"] else "")
        bits  = [it["label"]] if it["label"] else []
        if it["descr"]:
            bits.append(it["descr"])
        desc  = (" \u00b7 ".join(bits) or "Kouta Deals")[:200]
        go    = SITE + "/#/p/" + it["slug"]
        here  = os.path.join(pdir, it["slug"])
        os.makedirs(here, exist_ok=True)
        with open(os.path.join(here, "index.html"), "w", encoding="utf-8") as f:
            f.write(PRODUCT_PAGE.format(
                title=e(title), desc=e(desc),
                url=e(SITE + "/p/" + it["slug"] + "/"),
                img=e(SITE + "/" + it["_big"][0]), alt=e(it["name"]),
                price=e(it["label"]), go=e(go), gojs=_json.dumps(go)))
    if all(os.path.exists(os.path.join(PHOTOS, n)) for n in SHARE_PHOTOS):
        write_share_card(OUT)            # only when the five share photos are there

    # ---- keep the two indexes the app and the next build rely on
    man = {"products": {}, "tiles": {}}
    for it in items:
        key = it["name"] + "|" + it["brand"]
        man["products"][key] = {"big": it["_big"], "thumb": it["_thm"]}
    man["tiles"] = dict(TILES_USED)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        _json.dump(man, f, ensure_ascii=False, indent=0)

    pj = {}
    for key, v in man["products"].items():
        pj[key.lower()] = [SITE + "/" + u for u in v["big"]]
    os.makedirs(os.path.join(OUT, "panel"), exist_ok=True)
    with open(os.path.join(OUT, "panel", "photos.json"), "w", encoding="utf-8") as f:
        _json.dump(pj, f, ensure_ascii=False, separators=(",", ":"))

    nimg  = len(os.listdir(IMGDIR))
    hbyte = os.path.getsize(os.path.join(OUT, "index.html"))
    ibyte = sum(os.path.getsize(os.path.join(IMGDIR, f)) for f in os.listdir(IMGDIR))
    print("models          : %d" % len(items))
    print("categories      : %s" % ", ".join("%s (%d)" % (c, cnt[c]) for c in cats))
    print("image files     : %d  (%.1f MB)" % (nimg, ibyte / 1e6))
    print("index.html      : %.0f KB" % (hbyte / 1024))
    if problems:
        print("\nflagged:")
        for p in problems:
            print("  - " + p)


PRODUCT_PAGE = u"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} &middot; Kouta Deals</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="product">
<meta property="og:site_name" content="Kouta Deals">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{url}">
<meta property="og:locale" content="fr_FR">
<meta property="og:image" content="{img}">
<meta property="og:image:secure_url" content="{img}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1000">
<meta property="og:image:height" content="1333">
<meta property="og:image:alt" content="{alt}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{img}">
<meta http-equiv="refresh" content="0; url={go}">
<script>location.replace({gojs});</script>
<style>body{{margin:0;background:#FBF6F1;color:#3E322C;padding:40px 24px;
  font-family:Georgia,"Times New Roman",serif;text-align:center}}
a{{color:#B08D57}}</style>
</head>
<body>
<p>{title}<br>{price}<br><a href="{go}">Voir sur Kouta Deals</a></p>
</body>
</html>
"""

TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Kouta Deals — Boutique</title>
<meta name="description" content="Kouta Deals — pyjamas, collants et linge de maison. Commandes par WhatsApp.">
<meta name="theme-color" content="#FBF6F1">
<meta name="format-detection" content="telephone=no">
<link rel="canonical" href="{{SITE}}/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Kouta Deals">
<meta property="og:title" content="Kouta Deals - Boutique">
<meta property="og:description" content="Iconic · Chic · Timeless">
<meta property="og:url" content="{{SITE}}/">
<meta property="og:locale" content="fr_FR">
<meta property="og:image" content="{{OGIMG}}">
<meta property="og:image:secure_url" content="{{OGIMG}}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Kouta Deals — Iconic · Chic · Timeless">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Kouta Deals - Boutique">
<meta name="twitter:description" content="Iconic · Chic · Timeless">
<meta name="twitter:image" content="{{OGIMG}}">
<link rel="image_src" href="{{OGIMG}}">
<meta itemprop="name" content="Kouta Deals - Boutique">
<meta itemprop="description" content="Iconic · Chic · Timeless">
<meta itemprop="image" content="{{OGIMG}}">
<style>
/* three views in one page: category menu, a category's products, the basket.
   Views are toggled by the URL hash so the phone back button works. */
:root {
  --bg:#FBF6F1; --surface:#FFFFFF; --fg:#3E322C; --muted:#8A7263; --line:#E6D7C8;
  --gold:#B08D57; --deep:#4A3B33; --ok:#1F7A4D; --okfg:#FFFFFF; --del:#C0392B;
  --display:"Caladea",Georgia,"Times New Roman",serif;
  --body:"Carlito","Segoe UI",system-ui,sans-serif;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#1A1512; --surface:#231C18; --fg:#EFE4DA; --muted:#B8A392; --line:#3A2E27;
  --gold:#D4AF78; --deep:#F3E9E0; --ok:#2E9A63; --okfg:#FFFFFF; --del:#F0857A; color-scheme:dark; } }
:root[data-theme="dark"] {
  --bg:#1A1512; --surface:#231C18; --fg:#EFE4DA; --muted:#B8A392; --line:#3A2E27;
  --gold:#D4AF78; --deep:#F3E9E0; --ok:#2E9A63; --okfg:#FFFFFF; --del:#F0857A; color-scheme:dark; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font-family:var(--body); margin:0;
       padding-inline:16px; padding-block:0 104px; }
.wrap { max-width:1100px; margin:0 auto; }
[hidden] { display:none!important; }

header { text-align:center; padding-block:30px 14px; }
.eyebrow { font-size:11px; letter-spacing:.42em; text-transform:uppercase; color:var(--gold); }
h1 { font-family:var(--display); font-weight:400; font-size:clamp(28px,8.5vw,44px); letter-spacing:.1em;
     line-height:1.08; margin:14px 0 8px; color:var(--deep); text-wrap:balance; }
.tag { font-family:var(--display); font-style:italic; color:var(--muted); font-size:15px; margin:0; }
.rule { width:54px; height:1px; background:var(--gold); margin:16px auto; }

/* category menu */
.tiles { display:grid; grid-template-columns:1fr; gap:10px; padding-block:10px 24px; }
@media (min-width:760px) { .tiles { grid-template-columns:1fr 1fr; gap:14px; } }
.tile { display:flex; flex-direction:row; align-items:center; gap:14px; text-decoration:none;
        color:inherit; background:var(--surface); border:1px solid var(--line);
        border-radius:3px; overflow:hidden; padding:10px 14px 10px 10px; }
.tile:hover { border-color:var(--gold); }
.tshot { flex:0 0 64px; width:64px; height:64px; border-radius:2px; overflow:hidden;
         background:var(--bg); display:block; }
.tshot img { width:100%; height:100%; object-fit:cover; display:block; }
.tmeta { flex:1 1 auto; min-width:0; display:flex; flex-direction:column; gap:3px; }
.tname { font-family:var(--display); font-size:17.5px; color:var(--deep); padding:0; line-height:1.2; }
.tnum { font-family:var(--display); font-style:italic; font-size:13.5px; color:var(--gold);
        padding:0; letter-spacing:0; text-transform:none; line-height:1.3; }
.tgo { flex:0 0 auto; color:var(--gold); font-size:24px; line-height:1; }

/* share + footer */
.share { text-align:center; border-top:1px solid var(--line); padding-block:24px 4px; }
.sharebtn { display:inline-flex; align-items:center; gap:9px; text-decoration:none;
            font-family:var(--display); font-size:16px; color:var(--deep);
            background:var(--surface); border:1px solid var(--gold); border-radius:999px;
            padding:11px 22px; }
.sharebtn:hover { background:var(--gold); color:#fff; }
.sharebtn svg { width:18px; height:18px; fill:currentColor; }
.share p { margin:10px 0 0; font-size:13px; color:var(--muted); }
footer .fnote { max-width:34em; margin-inline:auto; }
.smax { font-size:12.5px; color:var(--del); margin:5px 0 0; }

/* category view */
.bar { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line);
       padding-block:12px 12px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.back { font:inherit; font-size:13px; color:var(--muted); background:none; border:0; cursor:pointer;
        padding:0; text-decoration:none; }
.back:hover { color:var(--gold); }
.back.pill { border:1px solid var(--line); border-radius:999px; padding:7px 15px;
             background:var(--surface); white-space:nowrap; }
.back.pill:hover { border-color:var(--gold); color:var(--gold); }
.ctitle { font-family:var(--display); font-size:20px; color:var(--deep); margin:0; flex:1; min-width:0; }
.filters { display:flex; gap:8px; flex-wrap:wrap; width:100%; }
.chip { font:inherit; font-size:12.5px; letter-spacing:.06em; padding:6px 13px; border-radius:999px;
        border:1px solid var(--line); background:transparent; color:var(--muted); cursor:pointer; }
.chip[aria-pressed="true"] { background:var(--gold); border-color:var(--gold); color:#fff; }
.count { text-align:center; font-size:12px; color:var(--muted); letter-spacing:.08em; padding-top:14px; }

.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:20px; padding-block:18px; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:3px;
        display:flex; flex-direction:column; min-width:0; overflow:hidden; }
.card[data-on="1"] { border-color:var(--ok); box-shadow:0 0 0 1px var(--ok); }
.shot { aspect-ratio:3/4; background:var(--bg); position:relative; }
.shot img { width:100%; height:100%; object-fit:cover; display:block; }
.tick { position:absolute; top:10px; right:10px; width:30px; height:30px; border-radius:50%;
        background:var(--ok); color:var(--okfg); display:none; align-items:center;
        justify-content:center; font-size:16px; line-height:1; }
.card[data-on="1"] .tick { display:flex; }
.gallery { display:flex; gap:6px; padding:10px 14px 0; overflow-x:auto; }
.gallery:empty { display:none; }
.th { padding:0; border:1px solid var(--line); background:none; border-radius:2px; cursor:pointer;
      flex:0 0 auto; line-height:0; }
.th.on { border-color:var(--gold); }
.th img { width:56px; height:74px; object-fit:cover; display:block; }
.meta { padding:12px 14px 16px; display:flex; flex-direction:column; gap:8px; }
.brand { margin:0; font-size:9.5px; letter-spacing:.3em; text-transform:uppercase; color:var(--gold); }
h3 { font-family:var(--display); font-weight:400; font-size:16px; line-height:1.25; margin:0; color:var(--deep); }
.opts { display:flex; align-items:center; gap:10px; }
.sz { display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--muted); flex:1; min-width:0; }
.sz.solo { flex:0 0 auto; }
.qty { display:flex; align-items:center; flex:0 0 auto; border:1px solid var(--line);
       border-radius:2px; background:var(--bg); overflow:hidden; }
.qty.solo { flex:1; }
.qb { font:inherit; font-size:16px; line-height:1; width:30px; height:31px; padding:0; border:0;
      background:none; color:var(--gold); cursor:pointer; }
.qb:hover { background:var(--gold); color:#fff; }
.qty [data-qty] { min-width:28px; flex:1; text-align:center; font-size:13.5px; color:var(--fg);
                  font-variant-numeric:tabular-nums; }
.rtxt .q { font-size:12.5px; color:var(--muted); }
.sz select { font:inherit; font-size:13px; color:var(--fg); background:var(--bg);
             border:1px solid var(--line); border-radius:2px; padding:5px 8px; flex:1; min-width:0; }
.price { font-family:var(--display); font-size:19px; color:var(--deep); margin:0;
         font-variant-numeric:tabular-nums;
         display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:8px; }
.price .pL { justify-self:start; min-width:0; }
.price .pR { justify-self:end; min-width:0; }
.price b { font-weight:inherit; justify-self:center; white-space:nowrap; }
.price s { color:#9b9089; font-size:15px; text-decoration-thickness:1px;
           white-space:nowrap; }
.price em.off { font-style:normal; font-size:12px; letter-spacing:.03em;
                background:#2f7d4f; color:#fff; border-radius:999px; padding:3px 9px;
                white-space:nowrap; }
.shr { position:absolute; top:10px; right:10px; z-index:4; width:36px; height:36px;
       display:grid; place-items:center; padding:0; border-radius:50%;
       background:var(--surface); border:1px solid var(--line); color:var(--fg);
       cursor:pointer; box-shadow:0 1px 4px rgba(0,0,0,.08); }
.shr svg { width:17px; height:17px; }
.shr:hover { border-color:var(--gold); color:var(--gold); }
.shr:active { transform:scale(.94); }
.card.sold .shr { display:none; }
.card.lit { box-shadow:0 0 0 2px var(--gold); }
.card.sold .shot img { filter:saturate(.55); opacity:.9; }
.card.sold .shot { position:relative; }
.card.sold .gallery { display:none; }
.soldtag { position:absolute; top:10px; left:10px; z-index:3;
           font-family:var(--body); font-size:9.5px; letter-spacing:.26em;
           text-transform:uppercase; background:var(--bg); color:var(--del);
           border:1px solid var(--del); border-radius:2px; padding:5px 10px; }
.soldline { margin:4px 0 0; font-size:11.5px; letter-spacing:.18em;
            text-transform:uppercase; color:var(--del); text-align:center; }
.card.sold .price { color:var(--muted); }
.soldhead { grid-column:1/-1; margin:26px 0 2px; text-align:center;
            font-size:10px; letter-spacing:.3em; text-transform:uppercase;
            color:var(--muted); }
.soldhead span { display:inline-block; border-top:1px solid var(--line);
                 padding-top:10px; min-width:230px; }
.more { align-self:flex-start; font:inherit; font-size:11.5px; letter-spacing:.06em;
        background:none; border:0; padding:0; margin:-2px 0 0; cursor:pointer;
        color:var(--gold); border-bottom:1px solid rgba(176,141,87,.45); }
.infowrap { position:fixed; inset:0; background:rgba(62,50,44,.45); z-index:90;
            display:flex; align-items:center; justify-content:center; padding:20px; }
.infobox { position:relative; background:var(--bg); border:1px solid var(--line);
           border-radius:4px; max-width:360px; width:100%; padding:20px 20px 24px;
           box-shadow:0 18px 50px rgba(0,0,0,.22); max-height:72vh; overflow:auto; }
.infox { position:sticky; top:0; float:right; margin:-6px -6px 0 0; width:34px; height:34px;
         border-radius:999px; border:1px solid var(--line); background:var(--surface);
         color:var(--deep); font-size:15px; line-height:1; cursor:pointer;
         box-shadow:0 2px 8px rgba(0,0,0,.10); }
.infobox .ib { margin:0 0 4px; font-size:9.5px; letter-spacing:.3em;
               text-transform:uppercase; color:var(--gold); }
.infobox h4 { font-family:var(--display); font-weight:400; font-size:19px; line-height:1.25;
              margin:0 0 14px; color:var(--deep); padding-right:36px; }
.infobox p.t { margin:0 0 12px; font-size:14px; line-height:1.65; color:var(--fg); }
.infobox p.t:last-child { margin-bottom:0; }
.pick { font:inherit; font-size:12.5px; letter-spacing:.08em; padding:10px; cursor:pointer;
        border:1px solid var(--gold); background:transparent; color:var(--gold); border-radius:2px; }
.card[data-on="1"] .pick { background:var(--ok); border-color:var(--ok); color:var(--okfg); }

/* basket */
.rows { list-style:none; margin:0; padding:0; border-top:1px solid var(--line); }
.row { display:flex; align-items:center; gap:12px; padding:12px 0; border-bottom:1px solid var(--line); }
.row img { width:54px; height:72px; object-fit:cover; border-radius:2px; flex:0 0 auto; }
.rtxt { flex:1; min-width:0; }
.rtxt .b { font-size:9.5px; letter-spacing:.3em; text-transform:uppercase; color:var(--gold); }
.rtxt .n { font-family:var(--display); font-size:15.5px; color:var(--deep); }
.rtxt .s { font-size:12.5px; color:var(--muted); }
.rtxt .s.miss { color:#B4551F; }
.rp { font-family:var(--display); font-size:16px; color:var(--deep); font-variant-numeric:tabular-nums;
      white-space:nowrap; }
.rx { font:inherit; font-size:19px; line-height:1; color:var(--del); background:none; border:0;
      cursor:pointer; padding:4px 6px; border-radius:3px; }
.rx:hover, .rx:focus-visible { background:var(--del); color:#fff; }
.tot { display:flex; justify-content:space-between; align-items:baseline; padding:18px 0 4px; }
.tot span { font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:var(--muted); }
.tot b { font-family:var(--display); font-weight:400; font-size:26px; color:var(--deep);
         font-variant-numeric:tabular-nums; }
.note { font-size:12.5px; color:var(--muted); margin:2px 0 18px; }
.checkout { display:flex; align-items:center; justify-content:center; gap:10px; width:100%;
            text-decoration:none; background:var(--ok); color:var(--okfg); border-radius:3px;
            padding:15px; font-size:15px; letter-spacing:.04em; }
.ic { width:20px; height:20px; flex:0 0 auto; }
.empty { text-align:center; color:var(--del); font-size:14px; padding-block:40px; }

footer { border-top:1px solid var(--line); margin-top:30px; padding-block:26px; text-align:center; }
footer p { margin:6px 0; font-size:13.5px; color:var(--muted); }
footer .num { font-family:var(--display); font-size:17px; color:var(--deep); font-variant-numeric:tabular-nums; }
footer .num a { color:inherit; text-decoration:none; border-bottom:1px solid var(--line); padding-bottom:1px; }
footer .num a:hover { color:var(--gold); border-bottom-color:var(--gold); }

/* pinned basket summary */
.sumbar { position:fixed; left:0; right:0; bottom:0; z-index:9; background:var(--surface);
          border-top:1px solid var(--ok); display:flex; align-items:center; gap:14px;
          justify-content:center; flex-wrap:wrap;
          padding:12px 16px calc(12px + env(safe-area-inset-bottom,0px)); }
.sumbar p { margin:0; font-size:13px; color:var(--muted); }
.sumbar p b { font-family:var(--display); font-size:19px; color:var(--deep); font-weight:400;
              font-variant-numeric:tabular-nums; }
.sumbar a { text-decoration:none; background:var(--ok); color:var(--okfg); border-radius:2px;
            padding:11px 20px; font-size:13px; letter-spacing:.05em; }
@media (prefers-reduced-motion:reduce) { * { animation:none!important; transition:none!important; } }
</style>
</head>
<body>

<div class="wrap">

  <section id="home">
    <header>
      <p class="eyebrow">Catalogue 2026</p>
      <h1>KOUTA DEALS</h1>
      <p class="tag">Iconic &middot; Chic &middot; Timeless</p>
      <div class="rule"></div>
      <p class="tag">Choisissez une cat&eacute;gorie</p>
    </header>
    <nav class="tiles">
{{TILES}}</nav>
    <div class="share">
      <a class="sharebtn" href="https://wa.me/?text={{SHARE}}" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a10 10 0 0 0-8.6 15l-1.3 4.8 4.9-1.3A10 10 0 1 0 12 2zm5.8 14.2c-.2.7-1.4 1.3-2 1.4-.5.1-1.1.1-1.8-.1-.4-.1-1-.3-1.7-.6-3-1.3-4.9-4.3-5-4.5-.2-.2-1.2-1.6-1.2-3s.7-2.1 1-2.4c.2-.3.6-.4.8-.4h.6c.2 0 .4 0 .7.5l.9 2.2c.1.2.1.4 0 .6l-.4.6-.3.3c-.1.1-.3.3-.1.6.2.3.8 1.3 1.7 2.1 1.1 1 2.1 1.3 2.4 1.5.3.1.5.1.7-.1l.9-1.1c.2-.2.4-.2.6-.1l2.1 1c.3.1.5.2.5.4.1.1.1.6-.1 1.1z"/></svg>
        Partager avec vos amis
      </a>
      <p>Faites d&eacute;couvrir notre univers sur WhatsApp</p>
    </div>
    <footer>
      <p class="eyebrow">Commandes &amp; assistance sur WhatsApp</p>
      <p class="fnote">Commandez facilement sur WhatsApp et contactez-nous pour toute question ou besoin d&rsquo;aide.</p>
      <p class="num"><a href="https://wa.me/{{WA1R}}">{{WA2}}</a> &nbsp;&middot;&nbsp; <a href="https://wa.me/{{WA}}">{{WA3}}</a></p>
      <p>Livraison gratuite sur Essaouira</p>
    </footer>
  </section>

  <section id="cat" hidden>
    <div class="bar">
      <a class="back pill" href="#/">&larr; Toutes les cat&eacute;gories</a>
      <h2 class="ctitle" id="ctitle"></h2>
      <div class="filters" id="filters"></div>
    </div>
    <p class="count" id="count"></p>
    <div class="grid" id="grid">
{{CARDS}}</div>
  </section>

  <section id="cart" hidden>
    <div class="bar">
      <button class="back pill" id="cback">&larr; Continuer mes achats</button>
      <h2 class="ctitle">Ma commande</h2>
    </div>
    <ul class="rows" id="rows"></ul>
    <div id="cartfoot" hidden>
      <div class="tot"><span>Total</span><b id="total"></b></div>
      <p class="note" id="cnote"></p>
      <a class="checkout" id="checkout" target="_blank" rel="noopener"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 3C6.98 3 3 6.58 3 11c0 2.3 1.08 4.36 2.82 5.8L5 21l4.3-1.7c.85.23 1.75.35 2.7.35 5.02 0 9-3.58 9-8s-3.98-8-9-8z"/></svg> Envoyer ma commande sur WhatsApp</a>
    </div>
    <p class="empty" id="cempty">Votre commande est vide.</p>
  </section>

</div>
<div class="sumbar" id="sumbar" hidden>
  <p id="sum"></p>
  <a href="#/panier">Voir ma commande</a>
</div>
<script>
(function(){
  var WA='{{WA}}', CUR='MAD', CATS=[{{CATS}}], RAW=[{{RAW}}];
  var SEAS=['Été', 'Demi-saison', 'Hiver'];
  var home=document.getElementById('home'), catv=document.getElementById('cat'),
      cartv=document.getElementById('cart');
  var cards=[].slice.call(document.querySelectorAll('.card'));
  var ctitle=document.getElementById('ctitle'), filters=document.getElementById('filters'),
      count=document.getElementById('count');
  var rows=document.getElementById('rows'), total=document.getElementById('total'),
      cartfoot=document.getElementById('cartfoot'), cempty=document.getElementById('cempty'),
      cnote=document.getElementById('cnote'), checkout=document.getElementById('checkout');
  var sumbar=document.getElementById('sumbar'), sum=document.getElementById('sum');
  var cur={cat:0, f:'all'};

  function money(n){ return n.toLocaleString('fr-FR')+' '+CUR; }

  // keep each card's own first photo for the basket thumbnail
  cards.forEach(function(c){
    var main=c.querySelector('.shot img');
    c._hero=main.getAttribute('src');
    c.querySelectorAll('.th').forEach(function(t){
      t.addEventListener('click',function(){
        main.src=t.dataset.src;
        c.querySelectorAll('.th').forEach(function(o){ o.classList.toggle('on',o===t); });
      });
    });
  });

  function picked(){ return cards.filter(function(c){ return c.dataset.on==='1'; }); }
  function sizeOf(c){ var s=c.querySelector('[data-size]'); return s&&s.value?s.value:''; }
  function qtyOf(c){ return Math.max(1, parseInt(c.dataset.qty||'1',10)||1); }
  function maxQty(c){ var s=parseInt(c.dataset.stock||'',10);
    return (isFinite(s)&&s>0)? s : 99; }
  function setQty(c,n){
    var mx=maxQty(c);
    n=Math.min(mx,Math.max(1,n));
    c.dataset.qty=String(n);
    var o=c.querySelector('[data-qty]'); if(o) o.textContent=n;
    var d=c.querySelector('[data-dec]'); if(d) d.disabled=(n<=1);
    var u=c.querySelector('[data-inc]'); if(u) u.disabled=(n>=mx);
    var w=c.querySelector('[data-max]'); if(w) w.hidden=!(n>=mx&&mx<99);
    refresh();
  }
  function lineTotal(c){ return (parseFloat(c.dataset.price)||0)*qtyOf(c); }
  function hasSize(c){ return !!c.querySelector('[data-size]'); }

  function message(list){
    var t=0, lines=[];
    list.forEach(function(c){
      var q=qtyOf(c); t+=lineTotal(c);
      var sz=sizeOf(c), det='';
      if(hasSize(c)) det=' ('+(sz?('taille '+sz):'taille \u00e0 pr\u00e9ciser')+')';
      lines.push('\u2022 '+(q>1?q+' \u00d7 ':'')+c.dataset.brand+' \u2014 '+c.dataset.name
                 +det+(c.dataset.price?' \u2014 '+money(lineTotal(c)):''));
    });
    return {total:t, text:'Bonjour Kouta Deals, je souhaite commander :\n'+lines.join('\n')
                        +'\n\nTotal : '+money(t)};
  }

  function drawCart(){
    var list=picked(); rows.innerHTML='';
    var m=message(list);
    list.forEach(function(c){
      var sz=sizeOf(c);
      var li=document.createElement('li'); li.className='row';
      var im=document.createElement('img'); im.src=c._hero; im.alt='';
      var tx=document.createElement('div'); tx.className='rtxt';
      tx.innerHTML='<div class="b"></div><div class="n"></div><div class="s"></div>';
      tx.querySelector('.b').textContent=c.dataset.brand;
      tx.querySelector('.n').textContent=c.dataset.name;
      var s=tx.querySelector('.s');
      if(hasSize(c)){
        s.textContent=sz?('Taille '+sz):'Taille \u00e0 pr\u00e9ciser';
        if(!sz) s.className='s miss';
      } else { s.textContent=''; }
      var q=qtyOf(c);
      var qd=document.createElement('div'); qd.className='q';
      qd.textContent=(q>1? q+' \u00d7 ' : '')+(c.dataset.label||'');
      tx.appendChild(qd);
      var p=document.createElement('div'); p.className='rp';
      p.textContent=c.dataset.price?money(lineTotal(c)):'';
      var x=document.createElement('button'); x.className='rx'; x.type='button';
      x.setAttribute('aria-label','Retirer'); x.textContent='\u00d7';
      x.addEventListener('click',function(){ setPick(c,false); });
      li.appendChild(im); li.appendChild(tx); li.appendChild(p); li.appendChild(x);
      rows.appendChild(li);
    });
    var any=list.length>0;
    cartfoot.hidden=!any; cempty.hidden=any;
    if(any){
      total.textContent=money(m.total);
      var miss=list.filter(function(c){ return hasSize(c) && !sizeOf(c); }).length;
      cnote.textContent=miss? (miss>1? miss+' articles sans taille \u2014 \u00e0 pr\u00e9ciser avec Kouta Deals'
                                     : '1 article sans taille \u2014 \u00e0 pr\u00e9ciser avec Kouta Deals')
                            : 'Livraison gratuite sur Essaouira';
      checkout.href='https://wa.me/'+WA+'?text='+encodeURIComponent(m.text);
    }
  }

  function refresh(){
    var list=picked();
    sumbar.hidden = !list.length || !cartv.hidden;
    if(list.length){
      var m=message(list);
      var n=list.reduce(function(a,c){ return a+qtyOf(c); },0);
      sum.innerHTML=n+(n>1?' articles':' article')
                    +' &nbsp;&middot;&nbsp; <b>'+money(m.total)+'</b>';
    }
    if(!cartv.hidden) drawCart();
  }

  function setPick(c,on){
    c.dataset.on=on?'1':'0';
    var b=c.querySelector('.pick');
    b.setAttribute('aria-pressed',String(on));
    b.querySelector('span').textContent=on?'\u2713 Ajout\u00e9':'Ajouter';
    refresh();
  }

  cards.forEach(function(c){
    var pk=c.querySelector('.pick');
    if(pk) pk.addEventListener('click',function(){
      setPick(c,c.dataset.on!=='1');
    });
    var s=c.querySelector('[data-size]');
    if(s) s.addEventListener('change',refresh);
    var dec=c.querySelector('[data-dec]'), inc=c.querySelector('[data-inc]');
    if(dec) dec.addEventListener('click',function(){ setQty(c,qtyOf(c)-1); });
    if(inc) inc.addEventListener('click',function(){ setQty(c,qtyOf(c)+1); });
    if(dec) dec.disabled=true;
  });

  function apply(){
    var n=0, firstSold=null;
    cards.forEach(function(c){
      var on = c.dataset.cat===RAW[cur.cat]
               && (cur.f==='all'||c.dataset.season===cur.f||c.dataset.season==='*');
      c.hidden=!on;
      if(on){
        if(c.dataset.sold==='1'){ if(!firstSold) firstSold=c; }
        else n++;
      }
    });
    var hd=document.getElementById('soldhead');
    if(!hd){
      hd=document.createElement('p');
      hd.id='soldhead'; hd.className='soldhead';
      hd.innerHTML='<span>D\u00e9j\u00e0 vendus</span>';
    }
    if(firstSold) firstSold.parentNode.insertBefore(hd,firstSold);
    else if(hd.parentNode) hd.parentNode.removeChild(hd);
    count.textContent=n+(n>1?' modèles':' modèle');
    [].slice.call(filters.children).forEach(function(ch){
      ch.setAttribute('aria-pressed',String(ch.dataset.f===cur.f));
    });
  }

  function openCat(i){
    cur.cat=i; cur.f='all';
    ctitle.textContent=CATS[i];
    var present=SEAS.filter(function(s){
      return cards.some(function(c){ return c.dataset.cat===RAW[i] && c.dataset.season===s; });
    });
    filters.innerHTML='';
    if(present.length>1){
      ['all'].concat(present).forEach(function(f){
        var b=document.createElement('button'); b.className='chip'; b.type='button';
        b.dataset.f=f; b.textContent=(f==='all'?'Tout':f);
        b.addEventListener('click',function(){ cur.f=f; apply(); });
        filters.appendChild(b);
      });
    }
    apply();
    home.hidden=true; cartv.hidden=true; catv.hidden=false;
    refresh(); window.scrollTo(0,0);
  }
  function openHome(){
    catv.hidden=true; cartv.hidden=true; home.hidden=false;
    refresh(); window.scrollTo(0,0);
  }
  function openCart(){
    home.hidden=true; catv.hidden=true; cartv.hidden=false;
    drawCart(); refresh(); window.scrollTo(0,0);
  }

  document.getElementById('cback').addEventListener('click',function(){
    if(history.length>1) history.back(); else location.hash='#/';
  });

  function route(){
    var h=location.hash||'#/';
    if(h.indexOf('#/c/')===0){
      var i=parseInt(h.slice(4),10);
      if(i>=0 && i<RAW.length) return openCat(i);
      return openHome();
    }
    if(h.indexOf('#/p/')===0){
      var sg=decodeURIComponent(h.slice(4)).replace(/[^A-Za-z0-9-]/g,'');
      var card=sg?document.querySelector('.card[data-slug="'+sg+'"]'):null;
      if(card){
        var ci=RAW.indexOf(card.getAttribute('data-cat'));
        openCat(ci<0?0:ci);
        setTimeout(function(){
          try{ card.scrollIntoView({block:'center'}); }catch(e){ card.scrollIntoView(); }
          card.classList.add('lit');
          setTimeout(function(){ card.classList.remove('lit'); },2600);
        },80);
        return;
      }
      return openHome();
    }
    if(h==='#/panier') return openCart();
    openHome();
  }

  /* ---- share one article ---- */
  document.addEventListener('click',function(ev){
    var b=(ev.target&&ev.target.closest)?ev.target.closest('.shr'):null;
    if(!b) return;
    ev.preventDefault(); ev.stopPropagation();
    var c=b.closest('.card'); if(!c) return;
    var nm=c.getAttribute('data-name')||'';
    var br=c.getAttribute('data-brand')||'';
    var pr=c.getAttribute('data-label')||'';
    var sg=c.getAttribute('data-slug')||'';
    var url='{{SITE}}/#/p/'+sg;
    var txt=nm+(br?' \u2014 '+br:'')+(pr?'\n'+pr:'');
    if(navigator.share){
      navigator.share({title:nm,text:txt,url:url})['catch'](function(){});
    }else{
      window.open('https://wa.me/?text='+encodeURIComponent(txt+'\n'+url),'_blank','noopener');
    }
  });
  window.addEventListener('hashchange',route);
  route();
})();

/* ---- "En savoir plus": the product description ---- */
(function(){
  var ov=null;
  function esc(s){ return String(s).replace(/[&<>"]/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function onKey(ev){ if(ev.key==='Escape') shut(true); }
  function shut(goBack){
    if(!ov) return;
    if(ov.parentNode) ov.parentNode.removeChild(ov);
    ov=null;
    document.removeEventListener('keydown',onKey);
    if(goBack){ try{ if(history.state && history.state.kdInfo) history.back(); }catch(e){} }
  }
  document.addEventListener('click',function(ev){
    var t=ev.target; if(!t || !t.closest) return;
    var b=t.closest('.more'); if(!b) return;
    var card=b.closest('article.card'); if(!card) return;
    var txt=card.getAttribute('data-descr')||''; if(!txt) return;
    shut(false);
    var paras=txt.split(/\n\s*\n/).map(function(p){
      return '<p class="t">'+esc(p).replace(/\n/g,'<br>')+'</p>'; }).join('');
    ov=document.createElement('div');
    ov.className='infowrap';
    ov.innerHTML='<div class="infobox" role="dialog" aria-modal="true">'
      +'<button class="infox" type="button" aria-label="Fermer">&#10005;</button>'
      +'<p class="ib">'+esc(card.getAttribute('data-brand')||'')+'</p>'
      +'<h4>'+esc(card.getAttribute('data-name')||'')+'</h4>'+paras+'</div>';
    document.body.appendChild(ov);
    ov.addEventListener('click',function(e2){ if(e2.target===ov) shut(true); });
    ov.querySelector('.infox').onclick=function(){ shut(true); };
    document.addEventListener('keydown',onKey);
    try{ history.pushState({kdInfo:1},''); }catch(e){}
  });
  window.addEventListener('popstate',function(){ if(ov) shut(false); });
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
