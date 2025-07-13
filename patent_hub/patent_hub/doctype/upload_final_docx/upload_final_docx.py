# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class UploadFinalDocx(Document):
	def before_insert(self):
		if not self.writer_id:
			frappe.throw(_("Writer ID is required to generate Upload Final Docx"))
		if not self.patent_id:
			frappe.throw(_("Patent ID is required to generate Upload Final Docx"))
		if not self.scene_to_tech_id:
			frappe.throw(_("Scene To Tech ID is required to generate Upload Final Docx"))
		if not self.tech_to_claims_id:
			frappe.throw(_("Tech To Claims ID is required to generate Upload Final Docx"))
		if not self.claims_to_docx_id:
			frappe.throw(_("Claims To Docx ID is required to generate Upload Final Docx"))
		if not self.docx_proofreading_id:
			frappe.throw(_("Docx Proofreading ID is required to generate Upload Final Docx"))
		_patent_id = self.patent_id.split("-")
		safe_name = _patent_id[1]
		self.upload_final_docx_id = make_autoname(f"UFD-{safe_name}-.##")
		self.name = self.upload_final_docx_id
