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
	compress_str_to_base64,
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

		mid_files = get_scene2tech_mid_files(doc)

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.scene2tech_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"base64file": compress_str_to_base64(doc.scene),
				"tmp_folder": tmp_folder,
				"mid_files": compress_json_to_base64(mid_files),
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
		doc.prior_solution_digest = _res.get("prior_solution_digest")
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


def get_scene2tech_mid_files(doc):
	mapping = {
		"core_problem_analysis": "1_core_problem_analysis.txt",
		"search_keywords_scene": "2.1_search_keywords.txt",
		"prior_art_scene": "2.2_prior_art.txt",
		"prior_solution_digest": "2.3_prior_solution_digest.txt",
		"patent_gap_analysis": "3_patent_gap_analysis.txt",
		"innovation_direction_0": "4_1.1_innovation_direction.txt",
		"design_00": "4_1.2_design_0.txt",
		"design_01": "4_1.2_design_1.txt",
		"innovation_direction_1": "4_2.1_innovation_direction.txt",
		"design_10": "4_2.2_design_0.txt",
		"design_11": "4_2.2_design_1.txt",
		"innovation_evaluation": "5_innovation_evaluation.txt",
		"patent_tech": "6_patent_tech.txt",
		"validation_report": "7_validation_report.txt",
		"final_tech": "tech.txt",
		"patentability_analysis_scene": "patentability.txt",
	}

	results = []
	for field, filename in mapping.items():
		content = getattr(doc, field, "")
		if content and content.strip():
			base64_str = compress_str_to_base64(content)
			results.append({"base64": base64_str, "original_filename": filename})

	return results
