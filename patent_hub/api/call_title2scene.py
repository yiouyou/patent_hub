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
	fail_task_fields,
	init_task_fields,
	universal_decompress,
)

logger = frappe.logger("app.patent_hub.patent_wf.call_title2scene")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Title2Scene] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		if doc.is_done_title2scene and not force:
			logger.info(f"[Title2Scene] 任务已完成，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		if doc.is_running_title2scene:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "title2scene", "T2S", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_title2scene._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)
		logger.info(f"[Title2Scene] 任务已提交队列: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Title2Scene] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Title2Scene] 开始执行任务: {docname}")
	doc = None
	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		# ⚠️ 再次检查是否已被取消
		if not doc.is_running_title2scene:
			logger.warning(f"[Title2Scene] 任务已被取消或终止，跳过执行: {docname}")
			return

		# 获取 API endpoint 和拼接 URL
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.title2scene.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Title2Scene] 请求 URL：{url}")

		# 临时工作路径
		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.title2scene_id)

		# 构建 payload
		payload = {
			"input": {
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
		_res = universal_decompress(output.get("res", ""))

		# 写入字段
		doc.scene = _res.get("scene")

		# ✅ 成功完成：标记任务完成状态
		complete_task_fields(
			doc,
			"title2scene",
			extra_fields={
				"time_s_title2scene": output.get("TIME(s)", 0.0),
				"cost_title2scene": output.get("cost", 0),
			},
		)

		logger.info(f"[Title2Scene] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("title2scene_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Title2Scene] 执行失败: {e}")
		logger.error(frappe.get_traceback())
		if doc:
			fail_task_fields(doc, "title2scene", str(e))
			frappe.db.commit()
			frappe.publish_realtime("title2scene_failed", {"error": str(e), "docname": docname}, user=user)
