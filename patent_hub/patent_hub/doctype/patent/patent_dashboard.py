from frappe import _


def get_data():
	return {
		"fieldname": "scene_to_tech_id",
		"transactions": [{"label": _("Related Scene To Tech"), "items": ["Scene To Tech ID"]}],
	}
