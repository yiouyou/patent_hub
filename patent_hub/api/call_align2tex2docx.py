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

logger = frappe.logger("app.patent_hub.patent_wf.call_align2tex2docx")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Align2Tex2Docx] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		# 已完成且非强制，则跳过
		if doc.is_done_align2tex2docx and not force:
			logger.warning(f"[Align2Tex2Docx] 任务已完成，未强制重跑，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		# 正在运行中，禁止重复提交
		if doc.is_running_align2tex2docx:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		# 初始化任务状态
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

		logger.info(f"[Align2Tex2Docx] 已入队: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Align2Tex2Docx] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Align2Tex2Docx] 开始执行任务: {docname}")
	doc = None

	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		# 🛡 防御性检查：任务被用户取消或意外退出，则跳过执行
		if not doc.is_running_align2tex2docx:
			logger.warning(f"[Align2Tex2Docx] 任务已非运行状态，跳过执行: {docname}")
			return

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.align2tex2docx.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Align2Tex2Docx] 请求 URL: {url}")

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.align2tex2docx_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"base64file": compress_str_to_base64(doc.application),
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

		logger.info(f"[Align2Tex2Docx] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("align2tex2docx_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Align2Tex2Docx] 执行失败: {e}")
		logger.error(frappe.get_traceback())

		if doc:
			fail_task_fields(doc, "align2tex2docx", str(e))
			frappe.db.commit()
			frappe.publish_realtime("align2tex2docx_failed", {"error": str(e), "docname": docname}, user=user)
