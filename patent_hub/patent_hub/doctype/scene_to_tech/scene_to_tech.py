# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class SceneToTech(Document):
	def before_insert(self):
		if not self.patent_id:
			frappe.throw(_("Patent ID is required to generate Scene To Tech"))
		_patent_id = self.patent_id.split("-")
		safe_name = _patent_id[1]
		self.scene_to_tech_id = make_autoname(f"S2T-{safe_name}-.##")
		self.name = self.scene_to_tech_id
