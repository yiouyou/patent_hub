from frappe import _


def get_data():
	return {
		"fieldname": "upload_final_docx_id",
		"transactions": [{"label": _("Related Upload Final Docx"), "items": ["Upload Final Docx ID"]}],
	}
