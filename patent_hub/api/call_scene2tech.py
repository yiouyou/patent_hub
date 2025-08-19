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
logger = frappe.logger("app.patent_hub.patent_wf.call_scene2tech")
logger.setLevel(logging.DEBUG)

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
	"""启动scene2tech任务"""
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
			SELECT name, is_done_scene2tech, is_running_scene2tech 
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
		if locked_doc.is_done_scene2tech and not force:
			raise ValueError("任务已完成，未重复执行")
		if locked_doc.is_running_scene2tech:
			raise ValueError("任务正在运行中，请等待完成")

		# 重新加载并更新状态
		doc.reload()
		init_task_fields(doc, "scene2tech", "S2T", logger)
		doc.save(ignore_permissions=True, ignore_version=True)


def _enqueue_task(docname: str, doc):
	"""入队任务并处理失败回滚"""
	try:
		return enqueue(
			"patent_hub.api.call_scene2tech._job",
			queue="long",
			timeout=TIMEOUT,
			job_name=f"scene2tech_{docname}",
			docname=docname,
			user=frappe.session.user,
		)
	except Exception as e:
		# 入队失败，回滚状态
		logger.error(f"入队失败，回滚状态: {e}")
		try:
			with atomic_transaction():
				doc.reload()
				doc.is_running_scene2tech = False
				doc.last_scene2tech_error = f"入队失败: {e}"
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
	"""执行scene2tech任务 - 自动心跳更新"""
	logger.info(f"开始执行任务: {docname}")
	doc = None

	try:
		# 验证任务状态
		with atomic_transaction():
			doc = frappe.get_doc("Patent Workflow", docname)

			# 使用数据库锁验证状态
			locked_status = frappe.db.sql(
				"""
				SELECT is_running_scene2tech 
				FROM `tabPatent Workflow` 
				WHERE name = %s 
				FOR UPDATE
				""",
				docname,
				as_dict=True,
			)

			if not locked_status or not locked_status[0].is_running_scene2tech:
				logger.warning(f"任务已非运行状态，跳过执行: {docname}")
				return

		# 构建API请求（在事务外执行）
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			raise ValueError("未配置 API Endpoint")

		url = f"{api_endpoint.server_ip_port.rstrip('/')}/{api_endpoint.scene2tech.strip('/')}/invoke"
		logger.info(f"请求 URL: {url}")

		# 准备请求数据
		mid_files = _get_scene2tech_mid_files(doc)
		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.scene2tech_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"base64file": text_to_base64(doc.scene),
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
			SELECT is_running_scene2tech 
			FROM `tabPatent Workflow` 
			WHERE name = %s 
			FOR UPDATE
			""",
			doc.name,
			as_dict=True,
		)

		if not locked_status or not locked_status[0].is_running_scene2tech:
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
			"core_problem_analysis": "core_problem_analysis",
			"search_keywords_scene": "search_keywords_scene",
			"prior_art_scene": "prior_art_scene",
			"prior_solution_digest": "prior_solution_digest",
			"patent_gap_analysis": "patent_gap_analysis",
			"innovation_direction_0": "innovation_direction_0",
			"design_00": "design_00",
			"design_01": "design_01",
			"innovation_direction_1": "innovation_direction_1",
			"design_10": "design_10",
			"design_11": "design_11",
			"innovation_evaluation": "innovation_evaluation",
			"patent_tech": "patent_tech",
			"validation_report": "validation_report",
			"final_tech": "final_tech",
			"patentability_analysis_scene": "patentability_analysis_scene",
		}

		# 批量更新字段
		for api_field, doc_field in field_mapping.items():
			if value := res_data.get(api_field):
				doc.set(doc_field, value)

		# 设置tech字段用于下一步
		if final_tech := res_data.get("final_tech"):
			doc.tech = final_tech

		# 完成任务
		complete_task_fields(
			doc,
			"scene2tech",
			{
				"time_s_scene2tech": output.get("TIME(s)", 0.0),
				"cost_scene2tech": output.get("cost", 0),
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
			{"docname": doc.name, "event": "scene2tech_done", "message": "Scene2Tech 任务完成"},
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

			fail_task_fields(doc, "scene2tech", error_msg, logger)
			doc.save(ignore_permissions=True, ignore_version=True)
	except Exception as save_error:
		logger.error(f"保存失败状态时出错: {save_error}")

	# 在事务外发布失败事件
	try:
		frappe.publish_realtime(
			"patent_workflow_update",
			{"docname": docname, "event": "scene2tech_failed", "error": error_msg},
			user=user,
		)
	except Exception as e:
		logger.warning(f"发布失败事件失败: {e}")


def _get_scene2tech_mid_files(doc) -> list[dict]:
	"""获取scene2tech中间文件"""
	field_to_filename = {
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
