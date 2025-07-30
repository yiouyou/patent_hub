import frappe


def execute(filters=None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns():
	return [
		{"label": "Step", "fieldname": "step", "fieldtype": "Data", "width": 250},
		{"label": "Total Cost", "fieldname": "total_cost", "fieldtype": "Currency", "width": 150},
		{"label": "Avg Cost/Patent", "fieldname": "avg_cost", "fieldtype": "Currency", "width": 180},
	]


def get_data():
	steps = [
		"total_cost_title2scene",
		"total_cost_info2tech",
		"total_cost_scene2tech",
		"total_cost_tech2application",
		"total_cost_align2tex2docx",
		"total_cost_review2revise",
	]

	step_name_map = {
		"total_cost_title2scene": "t2s",
		"total_cost_info2tech": "i2t",
		"total_cost_scene2tech": "s2t",
		"total_cost_tech2application": "t2a",
		"total_cost_align2tex2docx": "a2t2d",
		"total_cost_review2revise": "r2r",
	}

	total_patents = frappe.db.count("Patent Workflow")
	if total_patents == 0:
		total_patents = 1  # 防止除以0

	data = []

	for step in steps:
		total = (
			frappe.db.sql(f"""
				SELECT SUM(`{step}`) FROM `tabPatent Workflow`
			""")[0][0]
			or 0
		)
		avg = total / total_patents

		data.append(
			{
				"step": step_name_map.get(step, step),  # 使用映射名
				"total_cost": total,
				"avg_cost": avg,
			}
		)

	return data
