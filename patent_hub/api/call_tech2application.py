import asyncio
import base64
import json
import logging
import os
import re
import textwrap

import frappe
import httpx
from frappe import enqueue
from frappe.utils import add_to_date, now_datetime

from patent_hub.api._util_compression import decompress_file_from_base64, decompress_json_from_base64

logger = frappe.logger("app.patent_hub.patent_workflow.call_tech2application")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname):
	try:
		logger.info(f"开始处理文档：{docname}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		if doc.is_done_tech2application:
			return {"success": False, "error": "任务已完成，不可重复运行"}
		if doc.is_running_tech2application:
			return {"success": False, "error": "任务正在运行中，请等待完成"}
		doc.is_done_tech2application = 0
		doc.is_running_tech2application = 1
		doc.save()
		frappe.db.commit()
		enqueue(
			"patent_hub.api.call_tech2application._job",
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
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			frappe.throw(f"文档 {docname} 不存在")
		# 确保任务开始时设置正确的状态
		doc.is_done_tech2application = 0
		doc.is_running_tech2application = 1
		doc.save()
		frappe.db.commit()
		# 请求 URL
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")
		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.tech2application.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"请求 URL：{url}")
		# review_base64
		review_base64 = "test"
		# claims_base64
		claims_base64 = "test"
		# 拼接 tmp_folder
		tmp_folder = os.path.join(
			api_endpoint.get_password("server_work_dir"),
			re.sub(r"[^\w\u4e00-\u9fa5\-]", "", doc.patent_title),
			"r2r",
		)
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
		# logger.info(f"解析后的 JSON: {output}")
		_res = decompress_json_from_base64(output.get("res", ""))
		doc.tech_disclosure = _res["tech_disclosure"]
		doc.search_keywords_tech = _res["search_keywords_tech"]
		doc.prior_art_tech = _res["prior_art_tech"]
		doc.patentability_analysis_tech = _res["patentability_analysis_tech"]
		doc.prior_art_analysis = _res["prior_art_analysis"]
		doc.diff_analysis = _res["diff_analysis"]
		doc.claims_plan = _res["claims_plan"]
		doc.claims_science_optimized = _res["claims_science_optimized"]
		doc.claims_insufficiency_analysis = _res["claims_insufficiency_analysis"]
		doc.claims_insufficiency_optimized = _res["claims_insufficiency_optimized"]
		doc.claims_format_corrected = _res["claims_format_corrected"]
		doc.description_initial = _res["description_initial"]
		doc.description_innovation_analysis = _res["description_innovation_analysis"]
		doc.description_innovation_optimized = _res["description_innovation_optimized"]
		doc.description_science_analysis = _res["description_science_analysis"]
		doc.description_science_optimized = _res["description_science_optimized"]
		doc.description_abstract = _res["description_abstract"]
		doc.merged_application = _res["merged_application"]
		doc.refined_technical_solution7 = _res["refined_technical_solution7"]
		doc.final_application = _res["final_application"]
		doc.application = _res["final_application"]
		doc.time_s = output.get("TIME(s)", 0.0)
		doc.cost = output.get("cost", 0)
		doc.is_done_tech2application = 1
		doc.is_running_tech2application = 0
		doc.save()
		frappe.db.commit()
		frappe.publish_realtime("tech2application_done", {"docname": doc.name}, user=user)
	except Exception as e:
		logger.error(f"任务 tech2application 执行失败: {e!s}")
		logger.error(frappe.get_traceback())
		try:
			# 重置运行状态
			doc.is_done_tech2application = 0
			doc.is_running_tech2application = 0
			doc.save()
			frappe.db.commit()
			frappe.publish_realtime(
				"tech2application_failed", {"error": str(e), "docname": docname}, user=user
			)
		except Exception as save_error:
			logger.error(f"保存失败状态时出错: {save_error!s}")
