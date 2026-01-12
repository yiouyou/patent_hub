# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class PatentWorkflow(Document):
	def autoname(self):
		# 自动生成主键和 patent_id：PAT-YYYYMMDD-##
		self.name = make_autoname("PAT-.YYYY.MM.DD.-.##")
		self.patent_id = self.name

	def before_insert(self):
		if not self.writer_id:
			frappe.throw(_("Writer ID is required to generate Patent Workflow"))
		if not self.agency_id:
			frappe.throw(_("Agency ID is required to generate Patent Workflow"))
		if not self.patent_title:
			frappe.throw(_("Patent Title is required to generate Patent Workflow"))
		self.title = self.patent_title

	def validate(doc, method=None):
		set_current_stage(doc)


def set_current_stage(doc):
	# Title2Scene 流程链
	if doc.status_title2scene != "Done":
		doc.current_stage = "专利题目→应用分析（Title2Scene）"
	elif doc.status_scene2tech != "Done":
		doc.current_stage = "应用分析→技术交底（Scene2Tech）"
	# Info2Tech 流程链（可能与 Title2Scene 并行）
	elif doc.status_info2tech != "Done":
		doc.current_stage = "补充材料→技术交底（Info2Tech）"
	# 两条流程交汇于 Tech2Application
	elif doc.status_tech2application != "Done":
		doc.current_stage = "技术交底→申请txt（Tech2Application）"
	elif doc.status_align2tex2docx != "Done":
		doc.current_stage = "申请txt→申请docx（Align2Tex2Docx）"
	# 后期处理流程
	elif doc.status_proofreading != "Done":
		doc.current_stage = "校对中"
	elif doc.status_review2revise != "Done":
		doc.current_stage = "审查中"
	else:
		doc.current_stage = "已完成"
