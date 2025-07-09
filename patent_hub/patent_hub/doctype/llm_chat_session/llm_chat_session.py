# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class LLMChatSession(Document):
	def before_insert(self):
		if not self.llm_provider:
			frappe.throw(_("LLM Provider is required to generate Chat ID"))
		# 获取当前用户 Full Name
		user_id = frappe.session.user
		full_name = frappe.db.get_value("User", user_id, "full_name") or user_id
		clean_name = re.sub(r"[^\w\s-]", "", full_name)
		safe_name = re.sub(r"\s", "_", clean_name).replace("-", "_")
		self.chat_id = make_autoname(f"CHAT-{self.llm_provider}-{safe_name}-.YY.-.MM.-.DD.-.###")
		self.name = self.chat_id
