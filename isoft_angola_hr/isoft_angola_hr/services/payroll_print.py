# Copyright (c) 2026, ISOFT LDA
# Author: Abbass Chokor
# For license information, please see license.txt
"""Printable layout for the payroll preview.

The preview on screen is the source of truth. Nothing in this module recalculates,
rounds or re-derives a payroll figure: it receives the rows and the totals that the
preview already produced and decides only where they land on paper.

Why a whole module for a table
------------------------------
The previous implementation handed wkhtmltopdf a ``width:100%`` table with eighteen
columns and left the width allocation to the renderer. WebKit does not paginate a table
horizontally: when the content's minimum width exceeds the page it simply draws past the
right edge, and everything beyond the paper is never painted. The result was silent data
loss - INSS, IRT, Gross, Net and the employer INSS were missing from the PDF entirely,
with no error anywhere. A printed payroll that quietly omits five columns is worse than
one that fails to print.

So the width is no longer the renderer's decision. This module measures every string it
is about to print, in the metrics of the font it will actually be printed in, and:

  * fits the table to the printable width by construction (``table-layout: fixed`` with
    explicit column widths that sum to the page), so overflow is impossible rather than
    unlikely;
  * prints on a fixed :data:`PAPER` landscape sheet and picks the largest font from
    :data:`FONT_LADDER` that fits, so the same report comes out on the same paper every
    month whatever the figures happen to be;
  * splits the table into linked sections, each repeating the identity columns, if a
    column set ever cannot fit at all - a last resort, so that "drop a column" is never
    among the outcomes;
  * refuses to go below :data:`MIN_FONT_PT`, because a report nobody can read is not a
    report.

The font metrics are Helvetica's, which is what the PDF actually uses: wkhtmltopdf
resolves Arial/Helvetica to Liberation Sans, and Liberation Sans is metric-compatible
with both. Measuring against the wrong font would put us back to guessing.
"""

import unicodedata

import frappe
from frappe import _

#: Paper sizes in points (1pt = 1/72"), landscape, as wkhtmltopdf lays them out.
PAPER_PT = {"A4": (841.89, 595.28), "A3": (1190.55, 841.89)}

MARGIN_X_MM = 8.0
MARGIN_TOP_MM = 8.0
MARGIN_BOTTOM_MM = 12.0
MM_PT = 72.0 / 25.4

#: The payroll table is eighteen columns of currency; it does not fit A4 landscape at a
#: size anyone would read, so the report is A3 landscape and says so to the PDF engine.
PAPER = "A3"

#: Font sizes the planner will try, largest first.
FONT_LADDER = (9.0, 8.5, 8.0, 7.5, 7.0)
MIN_FONT_PT = 7.0

CELL_PAD_PT = 3.0
BORDER_PT = 0.6
#: Hinting, kerning and the odd glyph that is not in the metric table.
SAFETY = 1.04

#: Bounds for the columns that are allowed to wrap (employee, department).
TEXT_MIN_PT = 58.0
TEXT_MAX_PT = 116.0

#: Columns that identify the row. Repeated in every section of a split layout, so a
#: reader can always tell whose numbers they are looking at.
IDENTITY_KEYS = ("employee_name", "department", "days")


def _afm(spec):
	"""Expand ``{width: "chars"}`` into a per-character width table (per 1000 em)."""
	table = {}
	for width, chars in spec.items():
		for ch in chars:
			table[ch] = width
	return table


#: Helvetica advance widths, per 1000 em, for the printable ASCII range. Liberation
#: Sans - what wkhtmltopdf actually resolves Arial/Helvetica to - shares them exactly.
_REGULAR = _afm({
	222: "il'",
	278: ' !./:;I[]\\ft|j',
	333: "()-`r{}",
	355: '"',
	389: "*",
	469: "^",
	500: "JLckswvxyz",
	556: "0123456789$#_?abdeghnopqu",
	584: "+<=>~",
	611: "FTZ",
	667: "ABEKPSVXY&",
	722: "CDHNRUw",
	778: "GOQ",
	833: "Mm",
	889: "%",
	944: "W",
	1015: "@",
})
_REGULAR[","] = 278
_REGULAR["L"] = 556

_BOLD = _afm({
	278: " ./,il|",
	333: "!()-jft:;\\",
	389: "*{}r",
	474: '"',
	500: "z",
	556: "0123456789$#_^acksvxy[]'",
	584: "+<=>~",
	611: "EFLTZbdghnoqu?",
	667: "PSY",
	722: "ABCDHKNRUVX&",
	778: "GOQw",
	833: "M",
	889: "%m",
	944: "W",
	975: "@",
})
_BOLD["I"] = 278
_BOLD["J"] = 556
_BOLD["T"] = 611
_BOLD["e"] = 556
_BOLD["s"] = 556
_BOLD["t"] = 333

_DEFAULT_W = 600


def text_width(text, size_pt, bold=False):
	"""Width of ``text`` in points if it were set in Helvetica at ``size_pt``.

	Accented characters are measured as their base letter, which is exactly right in
	Helvetica and Liberation Sans: the accent is a zero-advance mark over the same glyph.
	"""
	if not text:
		return 0.0
	table = _BOLD if bold else _REGULAR
	total = 0
	for ch in unicodedata.normalize("NFD", str(text)):
		if unicodedata.combining(ch):
			continue
		total += table.get(ch, _DEFAULT_W)
	return total * size_pt / 1000.0


# ---------------------------------------------------------------------------
# layout planning
# ---------------------------------------------------------------------------

def _words(text):
	"""Break on whitespace only.

	Not on "/" - WebKit does not break there either, so treating "Paid/Total" as two
	tokens would size the column for "Total" and then split the header mid-word as
	"Paid/Tota" + "l Days", which is exactly what it did.
	"""
	return [w for w in str(text or "").split() if w]


def _column_demand(col, cells, size):
	"""How wide this column has to be, in points, at ``size``.

	Two regimes. A money or day column is printed ``white-space: nowrap`` - a figure
	broken across lines as ``Kz 30`` / ``000,00`` is what the old PDF did and is not
	readable - so it demands the width of its widest value. A text column may wrap, so
	it demands only enough to hold its longest single word, and is then given as much
	of the leftover width as there is to give.
	"""
	pad = 2 * CELL_PAD_PT + 2 * BORDER_PT
	header = str(col.get("label") or "")
	# the header itself wraps, so only its longest word is binding
	head = max([text_width(w, size, True) for w in _words(header)] or [0.0])

	if col.get("wraps"):
		longest_word = max([text_width(w, size) for c in cells for w in _words(c)] or [0.0])
		floor = max(longest_word, head)
		natural = max([text_width(c, size) for c in cells] or [0.0])
		return (min(max(floor, TEXT_MIN_PT), TEXT_MAX_PT) * SAFETY + pad,
		        min(max(natural, floor), TEXT_MAX_PT) * SAFETY + pad)

	body = max([text_width(c, size) for c in cells] or [0.0])
	need = max(body, head) * SAFETY + pad
	return need, need


def _fit(columns, display, size, avail):
	"""Column widths at ``size`` summing to exactly ``avail``, or None if they do not fit.

	``display`` is the fully formatted text of every cell that will be printed, totals
	row included: the widths are measured from what goes on the paper, not from a guess
	about what payroll figures usually look like.
	"""
	mins, wants = [], []
	for i, col in enumerate(columns):
		lo, hi = _column_demand(col, [r[i] for r in display], size)
		mins.append(lo)
		wants.append(max(hi, lo))

	if sum(mins) > avail:
		return None

	widths = list(mins)
	# first give the wrapping columns room to stop wrapping, then share what is left
	slack = avail - sum(widths)
	hungry = [i for i, c in enumerate(columns) if c.get("wraps") and wants[i] > widths[i]]
	if hungry and slack > 0:
		asked = sum(wants[i] - widths[i] for i in hungry)
		take = min(slack, asked)
		for i in hungry:
			widths[i] += (wants[i] - widths[i]) / asked * take
		slack -= take
	if slack > 0:
		# leftover width is worth nothing to a nowrap money column and everything to a
		# name column, which is the difference between a one-line and a three-line row
		wrapping = [i for i, c in enumerate(columns) if c.get("wraps")]
		if wrapping:
			give = slack * 0.75 / len(wrapping)
			for i in wrapping:
				widths[i] += give
			slack -= give * len(wrapping)
		share = slack / len(widths)
		for i in range(len(widths)):
			widths[i] += share
	# absorb the rounding remainder where it is least visible
	widths[-1] += avail - sum(widths)
	return widths


def _sections(columns, per_section=None):
	"""Split the columns into printable sections, each repeating the identity columns."""
	ident = [c for c in columns if c["key"] in IDENTITY_KEYS]
	rest = [c for c in columns if c["key"] not in IDENTITY_KEYS]
	if not rest:
		return [columns]
	size = per_section or max(1, (len(rest) + 1) // 2)
	return [ident + rest[i:i + size] for i in range(0, len(rest), size)]


def plan(columns, display, paper_ladder=(PAPER,)):
	"""Choose font size and column widths for this table on :data:`PAPER` landscape.

	The paper is fixed rather than chosen from the data. An earlier version tried A4
	first and fell back to A3, which meant a month with small figures printed on A4 and
	the next month, with one large one, silently printed on A3 - the same report on two
	different sheets. Payroll is filed and compared month to month, so predictability is
	worth more here than saving paper on a light month.

	Splitting into sections is the last resort, not the first, because a reader then has
	to carry a row across two tables. It exists so that no column can ever be dropped,
	whatever combination of columns is switched on.
	"""
	for paper in paper_ladder:
		width = PAPER_PT[paper][0]
		avail = width - 2 * MARGIN_X_MM * MM_PT
		for size in FONT_LADDER:
			if size < MIN_FONT_PT:
				break
			widths = _fit(columns, display, size, avail)
			if widths:
				return {"paper": paper, "font": size, "avail": avail,
				        "sections": [{"columns": columns, "widths": widths,
				                      "indexes": list(range(len(columns)))}]}

	# last resort: same paper, fewer columns per table
	paper = paper_ladder[-1]
	avail = PAPER_PT[paper][0] - 2 * MARGIN_X_MM * MM_PT
	for parts in (2, 3, 4):
		groups = _sections(columns, max(1, (len(columns) - len(IDENTITY_KEYS)) // parts))
		for size in FONT_LADDER:
			laid = []
			for g in groups:
				idx = [columns.index(c) for c in g]
				widths = _fit(g, [[r[i] for i in idx] for r in display], size, avail)
				if not widths:
					laid = None
					break
				laid.append({"columns": g, "widths": widths, "indexes": idx})
			if laid:
				return {"paper": paper, "font": size, "avail": avail, "sections": laid}

	frappe.throw(_("The selected payroll columns cannot be laid out on a readable page. "
	               "Hide some columns and export again."))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _css(p):
	"""Print stylesheet.

	The ``.print-format`` block is not decoration: frappe's ``read_options_from_html``
	greps the HTML for exactly these declarations and turns them into wkhtmltopdf
	margins, overwriting anything passed in ``options``. Each one has to stay on its own
	line - the regex that reads them is line-bound and greedy.
	"""
	return """
	/* wkhtmltopdf takes its paper from --page-size/--orientation, not from CSS; this
	   states the same page so the two cannot disagree, and so the HTML prints correctly
	   if it is ever opened in a browser. */
	@page {{ size: {paper} landscape; }}
	.print-format {{
		margin-top: {mt}mm;
		margin-bottom: {mb}mm;
		margin-left: {mx}mm;
		margin-right: {mx}mm;
	}}
	body {{ margin: 0; padding: 0; font-family: Helvetica, Arial, sans-serif;
	        color: #111; }}
	.pp-head {{ width: {avail}pt; margin: 0 0 6pt; }}
	.pp-head td {{ vertical-align: bottom; padding: 0; border: none; }}
	.pp-co {{ font-size: 13pt; font-weight: bold; letter-spacing: .2pt; }}
	.pp-nif {{ font-size: 8pt; color: #444; }}
	.pp-title {{ font-size: 10.5pt; font-weight: bold; }}
	.pp-meta {{ font-size: 8.5pt; color: #333; text-align: right; line-height: 1.35; }}
	.pp-rule {{ width: {avail}pt; height: 1.4pt; background: #1f3864; margin: 0 0 7pt; }}
	.pp-sec {{ font-size: 9.5pt; font-weight: bold; margin: 0 0 4pt; color: #1f3864; }}
	table.pp {{ width: {avail}pt; table-layout: fixed; border-collapse: collapse;
	            font-size: {font}pt; }}
	table.pp th, table.pp td {{ border: {border}pt solid #9aa5b1; padding: {pad}pt;
	            line-height: 1.12; overflow: hidden; }}
	table.pp thead th {{ background: #e8edf6; color: #1f3864; font-size: {hfont}pt;
	            vertical-align: bottom; white-space: normal; word-wrap: break-word; }}
	table.pp thead th.num {{ text-align: right; }}
	table.pp thead th.mid {{ text-align: center; }}
	table.pp thead th.txt {{ text-align: left; }}
	table.pp tr {{ page-break-inside: avoid; break-inside: avoid; }}
	table.pp thead {{ display: table-header-group; }}
	/* nowrap on the BODY cells only. Applying it to <th> as well is what clipped the
	   headers to "Food Allowanc" and "INSS Entidade Pa" - a header must wrap. */
	td.num {{ text-align: right; white-space: nowrap; }}
	td.mid {{ text-align: center; white-space: nowrap; }}
	td.txt {{ text-align: left; word-wrap: break-word; overflow-wrap: break-word; }}
	tr.pp-already td {{ background: #f4f6f9; color: #4a5568; }}
	.pp-legend {{ font-size: {sub}pt; color: #555; margin: 4pt 0 0; }}
	tr.pp-total td {{ background: #e8edf6; font-weight: bold; border-top: 1.4pt solid #1f3864; }}
	.pp-break {{ page-break-before: always; }}
	""".format(paper=PAPER, mt=MARGIN_TOP_MM, mb=MARGIN_BOTTOM_MM, mx=MARGIN_X_MM,
	           avail=round(p["avail"], 1), font=p["font"], hfont=round(p["font"] + 0.2, 1),
	           pad=CELL_PAD_PT, border=BORDER_PT, sub=round(p["font"] - 1.2, 1))


def _cls(col):
	if col.get("money"):
		return "num"
	return "mid" if col["key"] == "days" else "txt"


def _table(section, display, total_row, flags, esc):
	cols, widths, idx = section["columns"], section["widths"], section["indexes"]
	cg = "".join("<col style='width:{0}pt;'>".format(round(w, 2)) for w in widths)
	head = "".join("<th class='{0}'>{1}</th>".format(_cls(c), esc(c["label"])) for c in cols)
	body = []
	for n, row in enumerate(display):
		cells = "".join("<td class='{0}'>{1}</td>".format(_cls(cols[j]), esc(row[i]))
		                for j, i in enumerate(idx))
		flag = flags[n] if flags else ""
		body.append("<tr{0}>{1}</tr>".format(
			" class='pp-{0}'".format(flag) if flag else "", cells))
	foot = "<tr class='pp-total'>{0}</tr>".format("".join(
		"<td class='{0}'>{1}</td>".format(_cls(cols[j]), esc(total_row[i]))
		for j, i in enumerate(idx)))
	# the total row lives in the tbody on purpose: wkhtmltopdf repeats <tfoot> on every
	# page, which is why the old PDF printed the payroll total fourteen times.
	return ("<table class='pp'><colgroup>{cg}</colgroup><thead><tr>{head}</tr></thead>"
	        "<tbody>{body}{foot}</tbody></table>").format(
		cg=cg, head=head, body="".join(body), foot=foot)


def render_html(*, company_name, tax_id, period_label, employees, generated_label,
                columns, display, total_row, flags, plan_):
	"""Full print HTML for the payroll preview."""
	esc = frappe.utils.escape_html
	nif = ("<div class='pp-nif'>{0} {1}</div>".format(esc(_("NIF")), esc(tax_id))
	       if tax_id else "")
	meta = "<div>{0} {1}</div><div>{2} {3}</div><div>{4}</div>".format(
		esc(_("Period:")), esc(period_label), esc(_("Employees:")), employees,
		esc(generated_label))

	legend = ""
	if any(f == "already" for f in (flags or [])):
		legend = "<div class='pp-legend'>{0}</div>".format(
			esc(_("Shaded rows already have a salary slip for this period.")))

	tables = []
	multi = len(plan_["sections"]) > 1
	for n, section in enumerate(plan_["sections"]):
		label = ""
		if multi:
			label = "<div class='pp-sec'>{0}</div>".format(esc(_("Section {0} of {1}").format(
				n + 1, len(plan_["sections"]))))
		tables.append("<div class='{0}'>{1}{2}</div>".format(
			"pp-break" if n else "", label, _table(section, display, total_row, flags, esc)))

	return """<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<table class="pp-head"><tr>
	<td><div class="pp-co">{co}</div>{nif}<div class="pp-title">{title}</div></td>
	<td class="pp-meta">{meta}</td>
</tr></table>
<div class="pp-rule"></div>
{tables}
{legend}
</body></html>""".format(css=_css(plan_), co=esc(company_name), nif=nif,
                         title=esc(_("Payroll Preview")), meta=meta,
                         tables="".join(tables), legend=legend)


def pdf_options(plan_, period_label, generated_label):
	"""wkhtmltopdf options. Page numbers come from wkhtmltopdf's own ``[page]``
	substitution rather than a footer template, because ``get_pdf`` disables JavaScript
	and a JavaScript footer would silently print nothing."""
	return {
		"page-size": plan_["paper"],
		"orientation": "Landscape",
		"margin-left": "{0}mm".format(MARGIN_X_MM),
		"margin-right": "{0}mm".format(MARGIN_X_MM),
		"margin-top": "{0}mm".format(MARGIN_TOP_MM),
		"margin-bottom": "{0}mm".format(MARGIN_BOTTOM_MM),
		"footer-left": period_label,
		"footer-center": _("Page [page] of [topage]"),
		"footer-right": generated_label,
		"footer-font-name": "Helvetica",
		"footer-font-size": "7",
		"footer-spacing": "4",
	}


def compose(*, columns, rows, totals, value_of, money, company, period_label,
            flag_of=None, generated_label=None):
	"""Everything except the call to wkhtmltopdf: (html, options, plan).

	Separated so the layout can be asserted on directly. A test that only ever looks at
	the finished PDF cannot say WHY a column vanished, and this is a module whose whole
	job is that it never does.

	``value_of(row, key)`` and ``money(v)`` are supplied by the caller so that the printed
	text is produced by exactly the same code that produces the spreadsheet export. This
	module never reads a payroll field itself, which is what keeps "the PDF shows what the
	preview shows" true rather than merely intended.
	"""
	cols = [{"key": k, "label": _(l), "money": bool(m),
	         "wraps": k in ("employee_name", "department")}
	        for (l, k, m) in columns]

	def text(row, col):
		v = value_of(row, col["key"])
		return money(v) if col["money"] else ("" if v is None else str(v))

	display = [[text(r, c) for c in cols] for r in rows]
	flags = [(flag_of(r) if flag_of else "") or "" for r in rows]
	total_row = []
	for i, c in enumerate(cols):
		if i == 0:
			total_row.append(_("TOTAL"))
		elif c["money"]:
			total_row.append(money(totals.get(c["key"], 0.0)))
		else:
			total_row.append("")

	plan_ = plan(cols, display + [total_row])
	if generated_label is None:
		generated_label = "{0} {1}".format(
			_("Generated"), frappe.utils.format_datetime(frappe.utils.now_datetime()))

	company_name = frappe.db.get_value("Company", company, "company_name") or company or ""
	tax_id = frappe.db.get_value("Company", company, "tax_id") if company else None

	html = render_html(company_name=company_name, tax_id=tax_id,
	                   period_label=period_label, employees=len(rows),
	                   generated_label=generated_label, columns=cols, display=display,
	                   total_row=total_row, flags=flags, plan_=plan_)
	return html, pdf_options(plan_, period_label, generated_label), plan_


def build(**kwargs):
	"""Render the payroll preview to PDF bytes."""
	from frappe.utils.pdf import get_pdf

	html, options, plan_ = compose(**kwargs)
	return get_pdf(html, options=options), plan_
