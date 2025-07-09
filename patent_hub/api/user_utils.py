import random
import string

import frappe
from frappe.utils.password import update_password


def generate_random_password(length=10):
	chars = string.ascii_letters + string.digits
	return "".join(random.choice(chars) for _ in range(length))


def create_patent_writer_user(email, full_name):
	# 如果用户已存在，返回 None
	if frappe.db.exists("User", email):
		return None, None
	password = generate_random_password()
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name or "Patent Writer",
			"role_profile_name": "Patent Writer",
			"module_profile": "Patent Writer",
			"send_welcome_email": 0,
			"enabled": 1,
		}
	)
	try:
		user.insert(ignore_permissions=True)
		update_password(user.name, password)
		frappe.msgprint(f"Create User for {full_name} ({email})", alert=True)
		frappe.logger("create_user").info(f"Create User for {full_name} ({email})")
	except Exception:
		frappe.logger("create_user").error(f"失败：Create User for {full_name} ({email})", exc_info=True)
	return user.name, password
