# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class MDToDocx(Document):
	def before_save(self):
		# 只有新建时才赋值（也可以根据 is_new 判断）
		if not self.name or not self.name.startswith("MD2DOCX-"):
			self.md_to_docx_id = make_autoname("MD2DOCX-.YY.-.MM.-.DD.-.###")
			self.name = self.md_to_docx_id
