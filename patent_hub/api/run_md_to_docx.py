import base64
import json
import logging
import os
import re

import frappe
import requests
from frappe import enqueue

logger = frappe.logger("app_patent_hub")  # 会写入 sites/{site}/logs/patent_md.log
logger.setLevel(logging.INFO)


TIMEOUT = 1800  # 30分钟最大运行时间


@frappe.whitelist()
def run(docname):
	frappe.logger().info(f"开始处理文档：{docname}")
	user = frappe.session.user
	enqueue("patent_hub.api.run_md_to_docx._job", queue="long", timeout=TIMEOUT, docname=docname, user=user)


def _job(docname, user=None):
	frappe.logger().info(f"进入 job: {docname}")
	doc = frappe.get_doc("MD To Docx", docname)
	if not doc:
		frappe.throw(f"文档 {docname} 不存在")
	api_endpoint = frappe.get_single("API Endpoint")
	if not api_endpoint:
		frappe.throw("未配置 API Endpoint")
	base_url = api_endpoint.server_ip_port.rstrip("/")
	path = api_endpoint.md_to_docx.strip("/")
	url = f"{base_url}/{path}/invoke"
	# 编码 markdown
	markdown_text = doc.markdown or ""
	md_base64 = base64.b64encode(markdown_text.encode("utf-8")).decode("utf-8")
	# 提取标题作为文件夹名
	_match = re.search(r"^#\s*(.+)", markdown_text, re.MULTILINE)
	_title = _match.group(1).strip() if _match else "tmp"
	_title = re.sub(r"[^\w\u4e00-\u9fa5\-]", "", _title)  # 去除标点，保留连字符、中文、字母、数字
	# 拼接 tmp_folder
	server_work_dir = api_endpoint.get_password("server_work_dir")
	tmp_folder = os.path.join(server_work_dir, _title, "md2docx")
	payload = {"input": {"md_base64": md_base64, "tmp_folder": tmp_folder}}
	try:
		logger.info(f"请求 URL：{url}")
		res = requests.post(url, json=payload, timeout=TIMEOUT)
		logger.info(f"HTTP 状态码: {res.status_code}")
		logger.info(f"响应内容: {res.text}")
		res.raise_for_status()
		res_json = res.json()
		logger.info(f"res_json: {res_json}")
		# output
		output = json.loads(res_json["output"])
		logger.info(f"解析后的 JSON: {output}")
		logger.info(f"cost: {output['cost']}")
		logger.info(f"TIME(s): {output['TIME(s)']}")
		logger.info(f"generated_files: {output['generated_files']}")
		doc.time_s = output.get("TIME(s)", 0.0)
		doc.cost = output.get("cost", 0)
		urls = output.get("generated_files", [])
		if urls:
			# 清空并写入新文件列表
			doc.set("generated_files", [])
			for file_url in urls:
				doc.append("generated_files", {"file_s3_url": file_url})
			doc.is_done = 1
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime("md_to_docx_done", {"docname": doc.name}, user=user)
		else:
			logger.error("API 返回为空：未生成任何文件")
	except Exception as e:
		logger.error(frappe.get_traceback(), "MD to Docx API 调用失败")
		doc.set("generated_files", [])
		doc.append("generated_files", {"file_s3_url": "失败"})
		doc.is_done = 0
		doc.save()
		frappe.db.commit()
		frappe.publish_realtime("md_to_docx_failed", {"error": str(e), "docname": docname}, user=user)
