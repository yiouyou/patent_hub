# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from patent_hub.config.s3_bucket import S3_BUCKET_NAME


class TechToClaims(Document):
	def before_insert(self):
		if not self.patent_id:
			frappe.throw(_("Patent ID is required to generate Tech To Claims"))
		if not self.scene_to_tech_id:
			frappe.throw(_("Scene To Tech ID is required to generate Tech To Claims"))
		_patent_id = self.patent_id.split("-")
		safe_name = _patent_id[1]
		self.tech_to_claims_id = make_autoname(f"T2C-{safe_name}-.##")
		self.name = self.tech_to_claims_id
