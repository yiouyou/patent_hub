import asyncio
import base64
import json
import logging
import os
import re

import frappe
import httpx
from frappe import enqueue
from frappe.utils import now_datetime

from patent_hub.api._utils import (
	complete_task_fields,
	compress_str_to_base64,
	decompress_json_from_base64,
	fail_task_fields,
	init_task_fields,
)

logger = frappe.logger("app.patent_hub.patent_workflow.call_align2tex2docx")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname):
	try:
		logger.info(f"开始处理文档：{docname}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		# if doc.is_done_align2tex2docx:
		# 	return {"success": False, "error": "任务已完成，不可重复运行"}
		# if doc.is_running_align2tex2docx:
		# 	return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "align2tex2docx", "A2T2D", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_align2tex2docx._job",
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
	doc = None
	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.align2tex2docx.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"请求 URL：{url}")

		tmp_folder = os.path.join(
			api_endpoint.get_password("server_work_dir"),
			re.sub(r"[^\w\u4e00-\u9fa5\-]", "", doc.patent_title),
			"r2r",
		)

		payload = {
			"input": {
				"base64file": compress_str_to_base64(doc.application),
				"patent_title": doc.patent_title,
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

		doc.application_align = _res.get("application_align")
		doc.application_tex = _res.get("application_tex")
		doc.before_tex = _res.get("application_align")  # 原始对齐文本
		doc.figure_codes = "\n==========\n".join([str(code) for code in _res.get("figure_codes", [])])

		complete_task_fields(
			doc,
			"align2tex2docx",
			extra_fields={
				"time_s_align2tex2docx": output.get("TIME(s)", 0.0),
				"cost_align2tex2docx": output.get("cost", 0),
			},
		)
		frappe.db.commit()
		frappe.publish_realtime("align2tex2docx_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"任务 align2tex2docx 执行失败: {e}")
		logger.error(frappe.get_traceback())
		if doc:
			fail_task_fields(doc, "align2tex2docx", str(e))
			frappe.db.commit()
			frappe.publish_realtime("align2tex2docx_failed", {"error": str(e), "docname": docname}, user=user)


def get_base64_from_attachment(doc, fieldname):
	file_url = doc.get(fieldname)
	if not file_url:
		raise ValueError(f"字段 {fieldname} 为空，未上传文件")

	if file_url.startswith("/private/files/"):
		file_path = os.path.join(
			frappe.get_site_path("private", "files"), file_url.replace("/private/files/", "")
		)
	elif file_url.startswith("/files/"):
		file_path = os.path.join(frappe.get_site_path("public", "files"), file_url.replace("/files/", ""))
	else:
		raise ValueError(f"未知文件路径格式：{file_url}")

	with open(file_path, "rb") as f:
		return base64.b64encode(f.read()).decode("utf-8")
