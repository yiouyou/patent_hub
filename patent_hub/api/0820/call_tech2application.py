import asyncio
import json
import logging
import os
from contextlib import contextmanager
from typing import Any

import frappe
import httpx
from frappe import enqueue

from patent_hub.api._utils import (
	complete_task_fields,
	fail_task_fields,
	init_task_fields,
	text_to_base64,
	universal_compress,
	universal_decompress,
	with_heartbeat,
)

# 配置
logger = frappe.logger("app.patent_hub.patent_wf.call_tech2application")
# logger.setLevel(logging.DEBUG)

TIMEOUT = 1800
HTTP_CONFIG = {
	"timeout": httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
	"limits": httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0),
	"headers": {
		"User-Agent": "PatentHub/1.0",
		"Accept": "application/json",
		"Content-Type": "application/json",
	},
}


@contextmanager
def atomic_transaction():
	"""原子事务上下文管理器"""
	try:
		frappe.db.begin()
		yield
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise


@frappe.whitelist()
def run(docname: str, force: bool = False):
	"""启动tech2application任务"""
	try:
		logger.info(f"启动任务: {docname}, force={force}")

		# 获取文档并检查权限
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}
		doc.check_permission("write")

		# 原子性检查和更新状态
		try:
			_update_task_status(doc, force)
		except ValueError as e:
			logger.warning(f"任务状态检查失败 [{docname}]: {e}")
			return {"success": False, "error": str(e)}

		# 入队任务
		job = _enqueue_task(docname, doc)

		logger.info(f"任务已入队: {docname}, job_id: {job.id}")
		return {"success": True, "message": "任务已提交执行队列", "job_id": job.id}

	except frappe.PermissionError:
		logger.warning(f"权限不足: {docname}, user: {frappe.session.user}")
		return {"success": False, "error": "权限不足"}
	except Exception as e:
		logger.error(f"启动任务失败 [{docname}]: {e}")
		return {"success": False, "error": f"启动任务失败: {e!s}"}


def _update_task_status(doc, force: bool):
	"""原子性更新任务状态"""
	with atomic_transaction():
		# 使用 SELECT FOR UPDATE 锁定记录，防止并发修改
		locked_doc = frappe.db.sql(
			"""
			SELECT name, is_done_tech2application, is_running_tech2application 
			FROM `tabPatent Workflow` 
			WHERE name = %s 
			FOR UPDATE
			""",
			doc.name,
			as_dict=True,
		)

		if not locked_doc:
			raise ValueError(f"文档 {doc.name} 不存在")

		locked_doc = locked_doc[0]

		# 检查任务状态
		if locked_doc.is_done_tech2application and not force:
			raise ValueError("任务已完成，未重复执行")
		if locked_doc.is_running_tech2application:
			raise ValueError("任务正在运行中，请等待完成")

		# 重新加载并更新状态
		doc.reload()
		init_task_fields(doc, "tech2application", "T2A", logger)
		doc.save(ignore_permissions=True, ignore_version=True)


def _enqueue_task(docname: str, doc):
	"""入队任务并处理失败回滚"""
	try:
		return enqueue(
			"patent_hub.api.call_tech2application._job",
			queue="long",
			timeout=TIMEOUT,
			job_name=f"tech2application_{docname}",
			docname=docname,
			user=frappe.session.user,
		)
	except Exception as e:
		# 入队失败，回滚状态
		logger.error(f"入队失败，回滚状态: {e}")
		try:
			with atomic_transaction():
				doc.reload()
				doc.is_running_tech2application = False
				doc.last_tech2application_error = f"入队失败: {e}"
				doc.save(ignore_permissions=True, ignore_version=True)
		except Exception as rollback_error:
			logger.error(f"状态回滚失败: {rollback_error}")
		raise Exception(f"任务入队失败: {e}")


async def call_chain_with_retry(url: str, payload: dict, max_retries: int = 5) -> dict[str, Any]:
	"""API调用重试机制"""
	for attempt in range(max_retries):
		try:
			async with httpx.AsyncClient(**HTTP_CONFIG) as client:
				logger.info(f"API调用尝试 {attempt + 1}/{max_retries}")
				response = await client.post(url, json=payload)

				if response.status_code == 200:
					logger.info(f"API调用成功，响应大小: {len(response.content)} 字节")
					return response.json()

				# 5xx错误重试，4xx错误直接抛出
				if response.status_code < 500:
					response.raise_for_status()

				logger.warning(f"服务器错误 {response.status_code}，将重试")
				if attempt == max_retries - 1:
					response.raise_for_status()

		except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
			logger.warning(f"网络错误 (尝试 {attempt + 1}): {e}")
			if attempt == max_retries - 1:
				raise
		except httpx.HTTPStatusError as e:
			if e.response.status_code < 500:
				raise  # 客户端错误不重试
			logger.warning(f"服务器错误 (尝试 {attempt + 1}): {e}")
			if attempt == max_retries - 1:
				raise

		# 指数退避
		if attempt < max_retries - 1:
			wait_time = 2**attempt
			logger.info(f"等待 {wait_time} 秒后重试...")
			await asyncio.sleep(wait_time)

	raise Exception("所有重试都失败了")


@with_heartbeat("align2tex2docx", "Patent Workflow")
def _job(docname: str, user=None):
	"""执行tech2application任务 - 自动心跳更新"""
	logger.info(f"开始执行任务: {docname}")
	doc = None

	try:
		# 验证任务状态
		with atomic_transaction():
			doc = frappe.get_doc("Patent Workflow", docname)

			# 使用数据库锁验证状态
			locked_status = frappe.db.sql(
				"""
				SELECT is_running_tech2application 
				FROM `tabPatent Workflow` 
				WHERE name = %s 
				FOR UPDATE
				""",
				docname,
				as_dict=True,
			)

			if not locked_status or not locked_status[0].is_running_tech2application:
				logger.warning(f"任务已非运行状态，跳过执行: {docname}")
				return

		# 构建API请求（在事务外执行）
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			raise ValueError("未配置 API Endpoint")

		url = f"{api_endpoint.server_ip_port.rstrip('/')}/{api_endpoint.tech2application.strip('/')}/invoke"
		logger.info(f"请求 URL: {url}")

		# 准备请求数据
		mid_files = _get_tech2application_mid_files(doc)
		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.tech2application_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"base64file": text_to_base64(doc.tech),
				"tmp_folder": tmp_folder,
				"mid_files": universal_compress(mid_files),
			}
		}

		# 调用API（长时间操作，在事务外执行）
		result = asyncio.run(call_chain_with_retry(url, payload))

		# 处理结果（在新事务中）
		_process_api_result(doc, result, user)
		logger.info(f"执行成功: {docname}")

	except Exception as e:
		logger.error(f"执行失败 [{docname}]: {e}")
		_handle_task_failure(doc, docname, str(e), user)


def _process_api_result(doc, result: dict, user):
	"""处理API结果并更新文档"""
	with atomic_transaction():
		# 锁定文档并验证状态
		locked_status = frappe.db.sql(
			"""
			SELECT is_running_tech2application 
			FROM `tabPatent Workflow` 
			WHERE name = %s 
			FOR UPDATE
			""",
			doc.name,
			as_dict=True,
		)

		if not locked_status or not locked_status[0].is_running_tech2application:
			logger.warning(f"任务在执行过程中被取消: {doc.name}")
			return

		# 重新加载文档获取最新状态
		doc.reload()

		# 解析API响应
		output = result.get("output")
		if not output:
			raise ValueError("API响应格式错误：缺少output字段")

		if isinstance(output, str):
			output = json.loads(output)

		res_data = universal_decompress(output.get("res", ""), as_json=True)

		# 字段映射
		field_mapping = {
			"tech_disclosure": "tech_disclosure",
			"search_keywords_tech": "search_keywords_tech",
			"prior_art_tech": "prior_art_tech",
			"patentability_analysis_tech": "patentability_analysis_tech",
			"prior_art_analysis": "prior_art_analysis",
			"diff_analysis": "diff_analysis",
			"claims_plan": "claims_plan",
			"claims_science_optimized": "claims_science_optimized",
			"claims_insufficiency_analysis": "claims_insufficiency_analysis",
			"claims_insufficiency_optimized": "claims_insufficiency_optimized",
			"claims_format_corrected": "claims_format_corrected",
			"description_initial": "description_initial",
			"description_innovation_analysis": "description_innovation_analysis",
			"description_innovation_optimized": "description_innovation_optimized",
			"description_science_analysis": "description_science_analysis",
			"description_science_optimized": "description_science_optimized",
			"description_abstract": "description_abstract",
			"merged_application": "merged_application",
			"refined_technical_solution": "refined_technical_solution",
			"final_application": "final_application",
		}

		# 批量更新字段
		for api_field, doc_field in field_mapping.items():
			if value := res_data.get(api_field):
				doc.set(doc_field, value)

		# 设置application字段用于下一步
		if final_application := res_data.get("final_application"):
			doc.application = final_application

		# 完成任务
		complete_task_fields(
			doc,
			"tech2application",
			{
				"time_s_tech2application": output.get("TIME(s)", 0.0),
				"cost_tech2application": output.get("cost", 0),
			},
			logger,
		)

		doc.save(ignore_permissions=True, ignore_version=True)

	# 在事务外发布成功事件
	_publish_success_event(doc, user)


def _publish_success_event(doc, user):
	"""发布成功事件"""
	try:
		frappe.publish_realtime(
			"patent_workflow_update",
			{"docname": doc.name, "event": "tech2application_done", "message": "Tech2Application 任务完成"},
			user=user,
		)
	except Exception as e:
		logger.warning(f"发布成功事件失败: {e}")


def _handle_task_failure(doc, docname: str, error_msg: str, user):
	"""处理任务失败"""
	try:
		with atomic_transaction():
			if doc:
				doc.reload()
			else:
				doc = frappe.get_doc("Patent Workflow", docname)

			fail_task_fields(doc, "tech2application", error_msg, logger)
			doc.save(ignore_permissions=True, ignore_version=True)
	except Exception as save_error:
		logger.error(f"保存失败状态时出错: {save_error}")

	# 在事务外发布失败事件
	try:
		frappe.publish_realtime(
			"patent_workflow_update",
			{"docname": docname, "event": "tech2application_failed", "error": error_msg},
			user=user,
		)
	except Exception as e:
		logger.warning(f"发布失败事件失败: {e}")


def _get_tech2application_mid_files(doc) -> list[dict]:
	"""获取tech2application中间文件"""
	field_to_filename = {
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

	files = []
	for field, filename in field_to_filename.items():
		if content := getattr(doc, field, ""):
			if content.strip():
				files.append(
					{
						"content": content,
						"original_filename": filename,
					}
				)

	logger.info(f"找到 {len(files)} 个中间文件")
	return files
