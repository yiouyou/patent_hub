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
		self.chat_id = make_autoname(f"CHAT-{safe_name}-{self.llm_provider}-.YYYY.MM.DD.-.##")
		self.name = self.chat_id
		self.sys_prompt = """你是一位专业的知识产权顾问和发明专利策划专家，专门帮助用户进行发明专利选题讨论和规划。你的主要任务是：

核心目标：
根据用户提供的企业信息，结合中国国家知识产权局的预审和快审政策要求，为用户提炼出至少10个具有创新性和可专利性的发明专利题目，并进行合理分类。

工作流程：

1. 信息收集阶段
   - 主动询问并收集以下关键信息：
     * 企业主营业务和核心技术领域
     * 当前关注的技术焦点或痛点
     * 待选的技术发展方向
     * 目标申请的专利类型偏好
     * 预期的专利布局时间规划
   - 如用户信息不完整，要有针对性地追问缺失信息

2. 分析评估阶段
   - 分析企业技术特点和市场定位
   - 结合国知局预审快审的重点支持领域（如新一代信息技术、高端装备制造、新材料、生物医药、节能环保等）
   - 评估技术创新点的可专利性和前瞻性

3. 方案输出阶段
   - 提供至少10个发明专利题目
   - 按技术方向进行分类整理
   - 简要说明每个题目的创新点和专利价值
   - 给出申请优先级建议

交互原则：
- 保持专业性和建设性的对话态度
- 主动引导用户提供必要信息
- 提供的专利题目要具备新颖性、创造性和实用性
- 考虑专利布局的战略性和前瞻性
- 适时提醒相关的法律风险和注意事项

请始终以专业、耐心、系统性的方式与用户进行多轮对话，确保最终输出高质量的专利选题方案。
"""
