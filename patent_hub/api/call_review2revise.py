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
	compress_str_to_base64,
	decompress_json_from_base64,
	fail_task_fields,
	get_compressed_base64_files,
	init_task_fields,
)

logger = frappe.logger("app.patent_hub.patent_wf.call_review2revise")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Review2Revise] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		if doc.is_done_review2revise and not force:
			logger.warning(f"[Review2Revise] 任务已完成，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		if doc.is_running_review2revise:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "review2revise", "R2R", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_review2revise._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)

		logger.info(f"[Review2Revise] 已入队执行: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Review2Revise] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Review2Revise] 开始执行任务: {docname}")
	doc = None

	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		# 🛡 防御性：如果任务已非运行状态，则跳过执行
		if not doc.is_running_review2revise:
			logger.warning(f"[Review2Revise] 任务状态已取消，跳过执行: {docname}")
			return

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.review2revise.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Review2Revise] 请求 URL: {url}")

		base64_files = get_compressed_base64_files(doc, "table_upload_review2revise")
		if not base64_files:
			frappe.throw("未上传任何审查意见 PDF 文件，无法继续执行")
		last_review_base64 = base64_files[-1].get("base64")
		if not last_review_base64:
			frappe.throw("最后一个审查意见文件的 base64 编码为空")

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.review2revise_id)

		payload = {
			"input": {
				"review_base64": last_review_base64,
				"claims_base64": compress_str_to_base64(doc.application_tex),
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

		doc.reply_review = _res.get("reply_review_txt")
		doc.revised_application = _res.get("revised_application_txt")

		complete_task_fields(
			doc,
			"review2revise",
			extra_fields={
				"time_s_review2revise": output.get("TIME(s)", 0.0),
				"cost_review2revise": output.get("cost", 0),
			},
		)

		logger.info(f"[Review2Revise] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("review2revise_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Review2Revise] 执行失败: {e}")
		logger.error(frappe.get_traceback())

		if doc:
			fail_task_fields(doc, "review2revise", str(e))
			frappe.db.commit()
			frappe.publish_realtime("review2revise_failed", {"error": str(e), "docname": docname}, user=user)
