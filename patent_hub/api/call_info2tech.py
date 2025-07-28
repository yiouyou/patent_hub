import asyncio
import base64
import json
import logging
import os
import re

import frappe
import httpx
from frappe import enqueue

from patent_hub.api._utils import (
	complete_task_fields,
	compress_json_to_base64,
	decompress_json_from_base64,
	fail_task_fields,
	get_compressed_base64_files,
	init_task_fields,
)

logger = frappe.logger("app.patent_hub.patent_workflow.call_info2tech")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Info2Tech] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		# 已完成但未强制，则跳过
		if doc.is_done_info2tech and not force:
			logger.warning(f"[Info2Tech] 任务已完成，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		# 正在运行中，不允许并发
		if doc.is_running_info2tech:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "info2tech", "I2T", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_info2tech._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)

		logger.info(f"[Info2Tech] 已入队执行: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Info2Tech] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Info2Tech] 开始执行任务: {docname}")
	doc = None

	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		# 🛡 若任务已取消或非运行状态，自动跳过
		if not doc.is_running_info2tech:
			logger.warning(f"[Info2Tech] 任务状态已取消，跳过执行: {docname}")
			return

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.info2tech.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Info2Tech] 请求 URL: {url}")

		base64_files = get_compressed_base64_files(doc, "table_upload_info2tech")
		if not base64_files:
			frappe.throw("未上传任何文件，无法继续执行")
		info_files = [
			{"base64": item["base64"], "original_filename": item["original_filename"]}
			for item in base64_files
		]

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.info2tech_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"info_files": compress_json_to_base64(info_files),
				"tmp_folder": tmp_folder,
			}
		}

		async def call_chain():
			async with httpx.AsyncClient(timeout=TIMEOUT) as client:
				return await client.post(url, json=payload)

		res = asyncio.run(call_chain())
		res.raise_for_status()
		output = json.loads(res.json()["output"])
		_res = decompress_json_from_base64(output.get("res", ""))

		doc.tech = _res.get("tech")

		complete_task_fields(
			doc,
			"info2tech",
			extra_fields={
				"time_s_info2tech": output.get("TIME(s)", 0.0),
				"cost_info2tech": output.get("cost", 0),
			},
		)

		logger.info(f"[Info2Tech] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("info2tech_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Info2Tech] 执行失败: {e}")
		logger.error(frappe.get_traceback())

		if doc:
			fail_task_fields(doc, "info2tech", str(e))
			frappe.db.commit()
			frappe.publish_realtime("info2tech_failed", {"error": str(e), "docname": docname}, user=user)
