from frappe import _


def get_data():
	return {
		"fieldname": "docx_proofreading_id",
		"transactions": [{"label": _("Related Upload Final Docx"), "items": ["Upload Final Docx"]}],
	}
