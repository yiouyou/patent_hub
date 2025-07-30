import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, add_months, now_datetime


def execute(filters=None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns():
	return [
		{
			"label": "Writer",
			"fieldname": "writer",
			"fieldtype": "Link",
			"options": "Patent Writer",
			"width": 200,
		},
		# {"label": "Full Name", "fieldname": "full_name", "fieldtype": "Data", "width": 200},
		{"label": "Cost (All Time)", "fieldname": "total_all", "fieldtype": "Currency", "width": 200},
		{"label": "Cost (Past Year)", "fieldname": "total_year", "fieldtype": "Currency", "width": 200},
		{
			"label": "Cost (Past Quarter)",
			"fieldname": "total_quarter",
			"fieldtype": "Currency",
			"width": 200,
		},
		{
			"label": "Cost (Past Month)",
			"fieldname": "total_month",
			"fieldtype": "Currency",
			"width": 200,
		},
		{"label": "Avg Cost/Patent", "fieldname": "avg_cost", "fieldtype": "Currency", "width": 200},
	]


def get_data():
	PatentWorkflow = frappe.qb.DocType("Patent Workflow")
	PatentWriter = frappe.qb.DocType("Patent Writer")

	now = now_datetime()
	one_year_ago = add_months(now, -12)
	one_quarter_ago = add_months(now, -3)
	one_month_ago = add_months(now, -1)

	cost_fields = [
		PatentWorkflow.total_cost_info2tech,
		PatentWorkflow.total_cost_scene2tech,
		PatentWorkflow.total_cost_tech2application,
		PatentWorkflow.total_cost_align2tex2docx,
		PatentWorkflow.total_cost_title2scene,
		PatentWorkflow.total_cost_review2revise,
	]

	all_writers = frappe.get_all("Patent Writer", fields=["name", "full_name", "creation"])

	data = []

	for writer in all_writers:
		writer_id = writer.name

		def sum_total_cost(date_filter=None):
			query = (
				frappe.qb.from_(PatentWorkflow)
				.select(Sum(sum(cost_fields)).as_("total"))
				.where(PatentWorkflow.writer_id == writer_id)
			)
			if date_filter:
				query = query.where(PatentWorkflow.creation >= date_filter)
			result = query.run()
			return result[0][0] or 0

		total_all = sum_total_cost()
		total_year = sum_total_cost(one_year_ago)
		total_quarter = sum_total_cost(one_quarter_ago)
		total_month = sum_total_cost(one_month_ago)

		total_patents = frappe.db.count("Patent Workflow", filters={"writer_id": writer_id})
		avg_cost = total_all / total_patents if total_patents else 0

		data.append(
			{
				"writer": writer_id,
				# "full_name": writer.full_name,
				"total_all": total_all,
				"total_year": total_year,
				"total_quarter": total_quarter,
				"total_month": total_month,
				"avg_cost": avg_cost,
			}
		)

	return data
