# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class PatentAgency(Document):
	def before_insert(self):
		if not self.agent_name:
			frappe.throw(_("Agent Name is required to generate Patent Agency"))
		# if not self.weixin:
		# 	frappe.throw(_("Weixin is required to generate Patent Agency"))
		safe_name = re.sub(r"[^\w\s-]", "", self.agent_name).replace(" ", "_")
		self.agency_id = make_autoname(f"AGY-{safe_name}-.##")
		self.name = self.agency_id
