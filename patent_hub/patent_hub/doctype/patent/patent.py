# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class Patent(Document):
	def before_insert(self):
		if not self.patent_name:
			frappe.throw(_("Patent Name is required to generate Patent ID"))
		clean_name = re.sub(r"[^\w\s-]", "", self.patent_name)
		safe_name = re.sub(r"\s", "_", clean_name).replace("-", "_")
		self.patent_id = make_autoname(f"PAT-{safe_name}-.##")
		try:
			api_key = frappe.get_single("API KEY")
			S3_BUCKET_NAME = api_key.get_password("s3_bucket_name")
			if not S3_BUCKET_NAME:
				frappe.throw(_("S3 Bucket Name is not configured in Cloud Storage Settings."))
		except frappe.DoesNotExistError:
			frappe.throw(_("Cloud Storage Settings DocType not found or not configured."))
		except Exception as e:
			frappe.throw(_(f"Error retrieving S3 Bucket Name from settings: {e}"))
		self.s3_uri = f"{S3_BUCKET_NAME}/{self.patent_id}/"
		self.name = self.patent_id
