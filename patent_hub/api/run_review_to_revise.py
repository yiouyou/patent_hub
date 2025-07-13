import asyncio
import base64
import json
import logging
import os
import re
import textwrap

import boto3
import frappe
import httpx
from frappe import enqueue
from frappe.utils import add_to_date, now_datetime

logger = frappe.logger("app_patent_hub")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname):
	try:
		logger.info(f"开始处理文档：{docname}")
		doc = frappe.get_doc("Review To Revise", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		if not doc.review_pdf:
			return {"success": False, "error": "Review PDF 不能为空，请先上传后再运行任务"}
		if doc.is_done:
			return {"success": False, "error": "任务已完成，不可重复运行"}
		if doc.is_running:
			return {"success": False, "error": "任务正在运行中，请等待完成"}
		doc.is_running = 1
		doc.save()
		frappe.db.commit()
		enqueue(
			"patent_hub.api.run_review_to_revise._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)
		return {"success": True, "message": "任务已成功提交"}
	except Exception as e:
		logger.error(f"启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def get_base64_from_attachment(doc, fieldname):
	file_url = doc.get(fieldname)
	if not file_url:
		raise ValueError(f"字段 {fieldname} 为空，未上传文件")
	# 确定是私有还是公共路径
	if file_url.startswith("/private/files/"):
		file_path = os.path.join(
			frappe.get_site_path("private", "files"), file_url.replace("/private/files/", "")
		)
	elif file_url.startswith("/files/"):
		file_path = os.path.join(frappe.get_site_path("public", "files"), file_url.replace("/files/", ""))
	else:
		raise ValueError(f"未知文件路径格式：{file_url}")
	# 读取并转换为 base64 字符串
	with open(file_path, "rb") as f:
		encoded_bytes = base64.b64encode(f.read())
		return encoded_bytes.decode("utf-8")


def _job(docname, user=None):
	logger.info(f"进入 job: {docname}")
	try:
		doc = frappe.get_doc("Review To Revise", docname)
		if not doc:
			frappe.throw(f"文档 {docname} 不存在")
		# 确保任务开始时设置正确的状态
		doc.is_running = 1
		doc.is_done = 0
		doc.save()
		frappe.db.commit()
		# 请求 URL
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")
		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.review_to_revise.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"请求 URL：{url}")
		# review_base64
		review_base64 = get_base64_from_attachment(doc, "review_pdf")
		# claims_base64
		claims_base64 = "test"
		# 标题
		patent_title = doc.patent_title
		_title = re.sub(r"[^\w\u4e00-\u9fa5\-]", "", patent_title)  # 去除标点，保留连字符、中文、字母、数字
		# 拼接 tmp_folder
		server_work_dir = api_endpoint.get_password("server_work_dir")
		tmp_folder = os.path.join(server_work_dir, _title, "r2r")
		# payload
		payload = {
			"input": {
				"review_base64": review_base64,
				"claims_base64": claims_base64,
				"tmp_folder": tmp_folder,
			}
		}

		async def call_chain():
			async with httpx.AsyncClient(timeout=TIMEOUT) as client:
				return await client.post(url, json=payload)

		res = asyncio.run(call_chain())
		res.raise_for_status()
		res_json = res.json()
		# output
		output = json.loads(res_json["output"])
		logger.info(f"解析后的 JSON: {output}")
		doc.time_s = output.get("TIME(s)", 0.0)
		doc.cost = output.get("cost", 0)
		# s3_urls
		s3_urls = output.get("generated_files", [])
		logger.info(f"S3 URL：{s3_urls}")
		doc.set("generated_files", [{"s3_url": u} for u in s3_urls])
		doc.is_done = 1
		doc.is_running = 0
		doc.save()
		frappe.db.commit()
		frappe.publish_realtime("review_to_revise_done", {"docname": doc.name}, user=user)
	except Exception as e:
		logger.error(f"任务 {docname} 执行失败: {e!s}")
		logger.error(frappe.get_traceback())
		try:
			# 更新文档状态为失败
			doc = frappe.get_doc("Review To Revise", docname)
			# error_msg
			error_msg = f"失败: {e!s}"
			short_error_msg = textwrap.shorten(error_msg, width=135, placeholder="...")
			doc.set("generated_files", [{"s3_url": short_error_msg}])
			# 重置运行状态
			doc.is_done = 0
			doc.is_running = 0
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime(
				"review_to_revise_failed", {"error": str(e), "docname": docname}, user=user
			)
		except Exception as save_error:
			logger.error(f"保存失败状态时出错: {save_error!s}")
