from frappe import _


def get_data():
	return {
		"fieldname": "claims_to_docx_id",
		"transactions": [{"label": _("Related Claims To Docx"), "items": ["Claims To Docx"]}],
	}
