"""Checks a laid-out PDF for page-flow mistakes: a page left mostly empty and a heading stranded at the
bottom of a page, alone or with a single line under it, with the rest of its content on the next. Found problems are logged, so a template change that brings
them back shows up when a report is made."""

HEADINGS = {"h1", "h2", "h3"}


def _text_boxes(box, inside=(), els=()):
    """(box, tags of the elements around it, those elements) for every piece of text in document order; a
    section heading's wrapper (class "head", which also holds the Grafana link) counts as a heading."""
    el = getattr(box, "element", None)
    tag = getattr(box, "element_tag", None)
    if tag and el is not None and "head" in (el.get("class") or "").split():
        tag = "h2"
    if tag and (not els or els[-1] is not el):
        inside, els = inside + (tag,), els + (el,)
    if getattr(box, "text", None) or "Replaced" in type(box).__name__:  # text, or a chart or image
        yield box, inside, els
    for child in getattr(box, "children", None) or []:
        yield from _text_boxes(child, inside, els)


def problems(document, min_fill=0.3):
    out = []
    pages = document.pages
    for n, page in enumerate(pages, 1):
        root = page._page_box.children[0]  # the document; the other children are the page's margin boxes
        texts = list(_text_boxes(root))
        following = {id(e) for _, _, els in _text_boxes(pages[n]._page_box.children[0]) for e in els} if n < len(pages) else set()
        if not texts:
            if n < len(pages):
                out.append(f"page {n} is empty")
            continue
        top = page._page_box.content_box_y()
        bottom = max(b.position_y + b.margin_height() for b, _, _ in texts)
        fill = (bottom - top) / page._page_box.height
        if n < len(pages) and fill < min_fill:
            out.append(f"page {n} is {fill:.0%} full and the rest moved to page {n + 1}")
        if n == len(pages):
            continue
        heads = [i for i, (_, tags, _) in enumerate(texts) if HEADINGS & set(tags)]
        if heads:
            i = heads[-1]
            while i > 0 and HEADINGS & set(texts[i - 1][1]):  # a heading over more than one text box
                i -= 1
            after = {round(b.position_y) for b, _, _ in texts[heads[-1] + 1 :]}
            # the block the heading belongs to: a problem only when it carries on over the page
            _, tags, els = texts[heads[-1]]
            k = max(j for j, t in enumerate(tags) if t in HEADINGS)
            block = els[k - 1] if k else None
            if len(after) < 2 and block is not None and id(block) in following:
                title = " ".join((getattr(b, "text", "") or "").strip() for b, _, _ in texts[i : heads[-1] + 1])
                out.append(f"heading '{title}' has {len(after)} line(s) under it at the bottom of page {n}")
    return out
