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

TIMEOUT = 4000


@frappe.whitelist()
def run(docname):
	try:
		logger.info(f"开始处理文档：{docname}")
		doc = frappe.get_doc("Tech To Claims", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		if not doc.tech:
			return {"success": False, "error": "Tech 不能为空，请先填写后再运行任务"}
		if doc.is_done:
			return {"success": False, "error": "任务已完成，不可重复运行"}
		if doc.is_running:
			return {"success": False, "error": "任务正在运行中，请等待完成"}
		doc.is_running = 1
		doc.save()
		frappe.db.commit()
		enqueue(
			"patent_hub.api.run_tech_to_claims._job",
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


def _job(docname, user=None):
	logger.info(f"进入 job: {docname}")
	try:
		doc = frappe.get_doc("Tech To Claims", docname)
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
		app_name = api_endpoint.tech_to_claims.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"请求 URL：{url}")
		# 编码 markdown
		markdown_text = doc.tech or ""
		base64file = base64.b64encode(markdown_text.encode("utf-8")).decode("utf-8")
		# 标题
		patent_title = doc.patent_title
		_title = re.sub(r"[^\w\u4e00-\u9fa5\-]", "", patent_title)  # 去除标点，保留连字符、中文、字母、数字
		# 拼接 tmp_folder
		server_work_dir = api_endpoint.get_password("server_work_dir")
		tmp_folder = os.path.join(server_work_dir, _title, "t2c")
		# payload
		payload = {
			"input": {"base64file": base64file, "patent_title": patent_title, "tmp_folder": tmp_folder}
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
		frappe.publish_realtime("tech_to_claims_done", {"docname": doc.name}, user=user)
	except Exception as e:
		logger.error(f"任务 {docname} 执行失败: {e!s}")
		logger.error(frappe.get_traceback())
		try:
			# 更新文档状态为失败
			doc = frappe.get_doc("Tech To Claims", docname)
			# error_msg
			error_msg = f"失败: {e!s}"
			short_error_msg = textwrap.shorten(error_msg, width=135, placeholder="...")
			doc.set("generated_files", [{"s3_url": short_error_msg}])
			# 重置运行状态
			doc.is_done = 0
			doc.is_running = 0
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime("tech_to_claims_failed", {"error": str(e), "docname": docname}, user=user)
		except Exception as save_error:
			logger.error(f"保存失败状态时出错: {save_error!s}")
