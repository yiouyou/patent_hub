from frappe import _


def get_data():
	return {
		"fieldname": "claims_to_docx_id",
		"transactions": [{"label": _("Related Docx Proofreading"), "items": ["Docx Proofreading"]}],
	}
