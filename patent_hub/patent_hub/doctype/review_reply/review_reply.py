# Copyright (c) 2026, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class ReviewReply(Document):
	def autoname(self):
		# 自动生成主键和 reviewreply_id：PAT-YYYYMMDD-##
		self.name = make_autoname("PAT-.YYYY.MM.DD.-.##")
