import base64
import json
import logging
import os
import re
import textwrap

import frappe
import requests
from frappe import enqueue

logger = frappe.logger("app_patent_hub")  # 会写入 sites/{site}/logs/app_patent_hub.log
logger.setLevel(logging.INFO)

TIMEOUT = 1800  # 30分钟最大运行时间


@frappe.whitelist()
def run(docname):
	"""启动 MD 转 DOCX 任务"""
	try:
		frappe.logger().info(f"开始处理文档：{docname}")
		# 获取文档并检查状态
		doc = frappe.get_doc("MD To Docx", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		# 检查是否已完成
		if doc.is_done:
			return {"success": False, "error": "任务已完成，不可重复运行"}
		# 检查是否正在运行
		if doc.is_running:
			return {"success": False, "error": "任务正在运行中，请等待完成"}
		# 设置运行状态
		doc.is_running = 1
		doc.save()
		frappe.db.commit()
		user = frappe.session.user
		enqueue(
			"patent_hub.api.run_md_to_docx._job", queue="long", timeout=TIMEOUT, docname=docname, user=user
		)
		return {"success": True, "message": "任务已成功提交"}
	except Exception as e:
		frappe.logger().error(f"启动任务失败: {e!s}")
		frappe.logger().error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e!s}"}


def _job(docname, user=None):
	"""后台任务执行函数"""
	frappe.logger().info(f"进入 job: {docname}")
	try:
		doc = frappe.get_doc("MD To Docx", docname)
		if not doc:
			frappe.throw(f"文档 {docname} 不存在")
		# 确保任务开始时设置正确的状态
		doc.is_running = 1
		doc.is_done = 0
		doc.save()
		frappe.db.commit()
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
		# 请求 URL
		logger.info(f"请求 URL：{url}")
		res = requests.post(url, json=payload, timeout=TIMEOUT)
		logger.info(f"HTTP 状态码: {res.status_code}")
		logger.info(f"响应内容: {res.text}")
		# raise_for_status
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
		# urls
		urls = output.get("generated_files", [])
		if urls:
			# 清空并写入新文件列表
			doc.set("generated_files", [])
			for file_url in urls:
				doc.append("generated_files", {"file_s3_url": file_url})
			# 任务成功完成
			doc.is_done = 1
			doc.is_running = 0
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime("md_to_docx_done", {"docname": doc.name}, user=user)
			logger.info(f"任务 {docname} 成功完成")
		else:
			logger.error("API 返回为空：未生成任何文件")
			raise Exception("API 返回为空：未生成任何文件")
	except Exception as e:
		logger.error(f"任务 {docname} 执行失败: {e!s}")
		logger.error(frappe.get_traceback())
		try:
			# 更新文档状态为失败
			doc = frappe.get_doc("MD To Docx", docname)
			doc.set("generated_files", [])
			# error_msg
			error_msg = f"失败: {e!s}"
			short_error_msg = textwrap.shorten(error_msg, width=135, placeholder="...")
			doc.append("generated_files", {"file_s3_url": short_error_msg})
			doc.is_done = 0
			doc.is_running = 0  # 重置运行状态
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime("md_to_docx_failed", {"error": str(e), "docname": docname}, user=user)
		except Exception as save_error:
			logger.error(f"保存失败状态时出错: {save_error!s}")
