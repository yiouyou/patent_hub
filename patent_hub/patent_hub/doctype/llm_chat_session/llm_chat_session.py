# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class LLMChatSession(Document):
	def before_insert(self):
		if not self.llm_provider:
			frappe.throw(_("LLM Provider is required to generate Chat ID"))
		self.chat_id = make_autoname(f"CHAT-{self.llm_provider}-.YY.-.MM.-.DD.-.###")
		self.name = self.chat_id
