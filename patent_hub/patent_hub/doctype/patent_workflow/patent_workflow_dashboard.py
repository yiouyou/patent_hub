from frappe import _


def get_data():
	return {
		"fieldname": "related_patent_workflow",
		"transactions": [
			{
				"label": _("Related Md2docx"),
				"items": ["Md2docx"],
			},
			{
				"label": _("Related Code2png"),
				"items": ["Code2png"],
			},
		],
	}
