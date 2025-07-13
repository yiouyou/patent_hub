from frappe import _


def get_data():
	return {
		"fieldname": "writer_id",
		"transactions": [{"label": _("Related Patent"), "items": ["Patent"]}],
	}
