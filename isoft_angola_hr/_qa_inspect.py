import frappe


def run():
    print("scrub:", repr(frappe.scrub("Recibo de Vencimentos - Angola")))
    m = frappe.get_meta("Print Format")
    for f in m.fields:
        print("  %-28s %-14s %s" % (f.fieldname, f.fieldtype, f.label))
    print("pdf page size:", frappe.db.get_single_value("Print Settings", "pdf_page_size"),
          "| letterhead:", frappe.db.get_single_value("Print Settings", "with_letterhead"))
    print("frappe version:", frappe.__version__)
    import erpnext
    print("erpnext version:", erpnext.__version__)
