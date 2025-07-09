from frappe import _


def get_data():
	return {
		"fieldname": "tech_to_claims_id",
		"transactions": [{"label": _("Related Tech To Claims"), "items": ["Tech To Claims ID"]}],
	}
