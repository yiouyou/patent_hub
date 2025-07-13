# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class ReviewToRevise(Document):
	def before_insert(self):
		if not self.writer_id:
			frappe.throw(_("Writer ID is required to generate Review To Revise"))
		if not self.patent_id:
			frappe.throw(_("Patent ID is required to generate Review To Revise"))
		if not self.scene_to_tech_id:
			frappe.throw(_("Scene To Tech ID is required to generate Review To Revise"))
		if not self.tech_to_claims_id:
			frappe.throw(_("Tech To Claims ID is required to generate Review To Revise"))
		if not self.claims_to_docx_id:
			frappe.throw(_("Claims To Docx ID is required to generate Review To Revise"))
		if not self.docx_proofreading_id:
			frappe.throw(_("Docx Proofreading ID is required to generate Review To Revise"))
		if not self.upload_final_docx_id:
			frappe.throw(_("Upload Final Docx ID is required to generate Review To Revise"))
		_patent_id = self.patent_id.split("-")
		safe_name = _patent_id[1]
		self.review_to_revise_id = make_autoname(f"R2R-{safe_name}-.##")
		self.name = self.review_to_revise_id

	def validate(self):
		if self.review_pdf:
			file_doc = frappe.get_doc("File", {"file_url": self.review_pdf})
			if file_doc.file_size > 10 * 1024 * 1024:
				frappe.throw("上传的 PDF 文件不能超过 10MB，请重新上传。")
