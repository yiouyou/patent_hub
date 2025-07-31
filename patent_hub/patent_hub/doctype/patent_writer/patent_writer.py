# Copyright (c) 2025, sz and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname

from patent_hub.api.user_utils import create_patent_writer_user


class PatentWriter(Document):
	def before_insert(self):
		if not self.email:
			frappe.throw(_("Email is required to generate Patent Writer"))
		if not self.full_name:
			frappe.throw(_("Full Name is required to generate Patent Writer"))
		safe_name = re.sub(r"[^\w\s-]", "", self.full_name).replace(" ", "_")
		self.writer_id = make_autoname(f"WTR-{safe_name}-.##")
		self.name = self.writer_id

	def on_submit(self):
		if not self.email:
			return
		user_name, password = create_patent_writer_user(self.email, self.full_name)
		if not user_name:
			return
		login_url = frappe.utils.get_url("/login")
		message = f"""<!DOCTYPE html>
<html>
  <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.8; padding: 20px;">
    <div style="max-width: 640px; margin: auto; border: 1px solid #e0e0e0; border-radius: 10px; padding: 30px; background-color: #fafafa;">
      <h2 style="color: #0066cc; margin-bottom: 20px;">欢迎加入一鸥游</h2>
      <p style="margin-bottom: 20px;">亲爱的 {self.full_name or "专利工程师"}，</p>
      <p style="margin-bottom: 20px;">
        您的"AI Frees You"专利工程师账号已成功创建。请使用以下信息登录系统：
      </p>
      <ul style="margin-bottom: 24px;">
        <li><strong>登录网址：</strong> <a href="{login_url}" style="color: #0066cc;">{login_url}</a></li>
        <li><strong>邮箱：</strong> {self.email}</li>
        <li><strong>初始密码：</strong> {password}</li>
      </ul>
      <p style="margin-bottom: 32px;"><em>请您首次登录后及时修改密码以保障账户安全。</em></p>
      <hr style="border: none; border-top: 1px solid #ddd; margin: 40px 0;">
      <p style="margin-bottom: 0;">
        此致敬礼，<br>
        <strong>AI Frees You团队</strong>
      </p>
    </div>
  </body>
</html>"""

		try:
			# 发送邮件
			frappe.sendmail(
				recipients=[self.email],
				subject="Welcome to Athenomics",
				message=message,
				delayed=False,
				retry=3,
			)
			frappe.msgprint(f"Send welcome email to {self.email} ({self.full_name})", alert=True)
			frappe.logger("send_email").info(f"Send welcome email to {self.email} ({self.full_name})")
		except Exception:
			frappe.logger("send_email").error(
				f"失败：Send welcome email to {self.email} ({self.full_name})", exc_info=True
			)
