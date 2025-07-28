import asyncio
import json
import logging
import os
import re

import frappe
import httpx
from frappe import enqueue

from patent_hub.api._utils import (
	complete_task_fields,
	decompress_json_from_base64,
	fail_task_fields,
	init_task_fields,
)

logger = frappe.logger("app.patent_hub.patent_workflow.call_scene2tech")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Scene2Tech] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		if doc.is_done_scene2tech and not force:
			logger.info(f"[Scene2Tech] 任务已完成，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		if doc.is_running_scene2tech:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "scene2tech", "S2T", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_scene2tech._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)

		logger.info(f"[Scene2Tech] 已入队执行: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Scene2Tech] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Scene2Tech] 开始执行任务: {docname}")
	doc = None

	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		# 🛡 防御性检查：任务已非运行状态，则跳过执行
		if not doc.is_running_scene2tech:
			logger.warning(f"[Scene2Tech] 任务已取消或中断，跳过执行: {docname}")
			return

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.scene2tech.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Scene2Tech] 请求 URL: {url}")

		tmp_folder = os.path.join(
			api_endpoint.get_password("server_work_dir"),
			re.sub(r"[^\w\u4e00-\u9fa5\-]", "", doc.patent_title),
			"r2r",
		)

		payload = {
			"input": {
				"review_base64": "test",
				"claims_base64": "test",
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

		# 填充结果字段
		doc.core_problem_analysis = _res.get("core_problem_analysis")
		doc.search_keywords_scene = _res.get("search_keywords_scene")
		doc.prior_art_scene = _res.get("prior_art_scene")
		doc.piror_solution_digest = _res.get("piror_solution_digest")
		doc.patent_gap_analysis = _res.get("patent_gap_analysis")
		doc.innovation_direction_0 = _res.get("innovation_direction_0")
		doc.design_00 = _res.get("design_00")
		doc.design_01 = _res.get("design_01")
		doc.innovation_direction_1 = _res.get("innovation_direction_1")
		doc.design_10 = _res.get("design_10")
		doc.design_11 = _res.get("design_11")
		doc.innovation_evaluation = _res.get("innovation_evaluation")
		doc.patent_tech = _res.get("patent_tech")
		doc.validation_report = _res.get("validation_report")
		doc.final_tech = _res.get("final_tech")
		doc.patentability_analysis_scene = _res.get("patentability_analysis_scene")

		# 用于下一步 tech 字段
		doc.tech = _res.get("final_tech")

		complete_task_fields(
			doc,
			"scene2tech",
			extra_fields={
				"time_s_scene2tech": output.get("TIME(s)", 0.0),
				"cost_scene2tech": output.get("cost", 0),
			},
		)

		logger.info(f"[Scene2Tech] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("scene2tech_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Scene2Tech] 执行失败: {e}")
		logger.error(frappe.get_traceback())
		if doc:
			fail_task_fields(doc, "scene2tech", str(e))
			frappe.db.commit()
			frappe.publish_realtime("scene2tech_failed", {"error": str(e), "docname": docname}, user=user)
