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

logger = frappe.logger("app.patent_hub.patent_wf.call_tech2application")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Tech2Application] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		if doc.is_done_tech2application and not force:
			logger.info(f"[Tech2Application] 任务已完成，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		if doc.is_running_tech2application:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "tech2application", "T2A", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_tech2application._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)
		logger.info(f"[Tech2Application] 任务已提交执行队列: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Tech2Application] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Tech2Application] 开始执行任务: {docname}")
	doc = None
	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		if not doc.is_running_tech2application:
			logger.warning(f"[Tech2Application] 任务已被取消或终止，跳过执行: {docname}")
			return

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.tech2application.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Tech2Application] 请求 URL: {url}")

		mid_files = get_tech2application_mid_files(doc)

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.tech2application_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"base64file": compress_str_to_base64(doc.tech),
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

		# 主字段写入
		doc.tech_disclosure = _res.get("tech_disclosure")
		doc.search_keywords_tech = _res.get("search_keywords_tech")
		doc.prior_art_tech = _res.get("prior_art_tech")
		doc.patentability_analysis_tech = _res.get("patentability_analysis_tech")
		doc.prior_art_analysis = _res.get("prior_art_analysis")
		doc.diff_analysis = _res.get("diff_analysis")
		doc.claims_plan = _res.get("claims_plan")
		doc.claims_science_optimized = _res.get("claims_science_optimized")
		doc.claims_insufficiency_analysis = _res.get("claims_insufficiency_analysis")
		doc.claims_insufficiency_optimized = _res.get("claims_insufficiency_optimized")
		doc.claims_format_corrected = _res.get("claims_format_corrected")
		doc.description_initial = _res.get("description_initial")
		doc.description_innovation_analysis = _res.get("description_innovation_analysis")
		doc.description_innovation_optimized = _res.get("description_innovation_optimized")
		doc.description_science_analysis = _res.get("description_science_analysis")
		doc.description_science_optimized = _res.get("description_science_optimized")
		doc.description_abstract = _res.get("description_abstract")
		doc.merged_application = _res.get("merged_application")
		doc.refined_technical_solution = _res.get("refined_technical_solution")
		doc.final_application = _res.get("final_application")
		doc.application = _res.get("final_application")

		# ✅ 标记完成
		complete_task_fields(
			doc,
			"tech2application",
			extra_fields={
				"time_s_tech2application": output.get("TIME(s)", 0.0),
				"cost_tech2application": output.get("cost", 0),
			},
		)

		logger.info(f"[Tech2Application] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("tech2application_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Tech2Application] 执行失败: {e}")
		logger.error(frappe.get_traceback())
		if doc:
			fail_task_fields(doc, "tech2application", str(e))
			frappe.db.commit()
			frappe.publish_realtime(
				"tech2application_failed", {"error": str(e), "docname": docname}, user=user
			)


def get_tech2application_mid_files(doc):
	mapping = {
		"tech_disclosure": "1_disclosure.txt",
		"search_keywords_tech": "2.1_search_keywords.txt",
		"prior_art_tech": "2.2_prior_art.txt",
		"prior_art_analysis": "2.3_prior_art_analysis.txt",
		"patentability_analysis_tech": "patentability.txt",
		"diff_analysis": "3_diff_analysis.txt",
		"claims_plan": "4.0_claims_plan.txt",
		"claims_science_optimized": "4.4_claims_science_optimized.txt",
		"claims_insufficiency_analysis": "4.5_claims_insufficiency_analysis.txt",
		"claims_insufficiency_optimized": "4.6_claims_insufficiency_optimized.txt",
		"claims_format_corrected": "4.7_claims_format_corrected.txt",
		"description_initial": "5.1_description_initial.txt",
		"description_innovation_analysis": "5.2_description_innovation_analysis.txt",
		"description_innovation_optimized": "5.3_description_innovation_optimized.txt",
		"description_science_analysis": "5.4_description_science_analysis.txt",
		"description_science_optimized": "5.5_description_science_optimized.txt",
		"description_abstract": "5.6_description_abstract.txt",
		"merged_application": "6_merged_application.txt",
		"refined_technical_solution": "7_refined_technical_solution.txt",
		"final_application": "application.txt",
	}

	results = []
	for field, filename in mapping.items():
		content = getattr(doc, field, "")
		if content and content.strip():
			base64_str = compress_str_to_base64(content)
			results.append({"base64": base64_str, "original_filename": filename})

	return results
