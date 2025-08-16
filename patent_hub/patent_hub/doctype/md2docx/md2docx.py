# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class Md2docx(Document):
	def autoname(self):
		# 自动生成主键和 md2docx_id：M2D-YYYYMMDD-##
		self.name = make_autoname("M2D-.YYYY.MM.DD.-.##")
		self.md2docx_id = self.name
