import asyncio
import json
import logging
import os
import tempfile
from typing import Any

import frappe
import httpx
from frappe import enqueue
from frappe.utils import now_datetime

from patent_hub.api._utils import (
	complete_task_fields,
	fail_task_fields,
	init_task_fields,
	restore_from_json_serializable,
	text_to_base64,
	universal_decompress,
)

logger = frappe.logger("app.patent_hub.patent_wf.call_align2tex2docx")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Align2Tex2Docx] 准备启动任务: {docname}, force={force}")

		# 🔧 添加权限检查
		doc = frappe.get_doc("Patent Workflow", docname)
		doc.check_permission("write")

		if doc.is_done_align2tex2docx and not force:
			logger.warning(f"[Align2Tex2Docx] 任务已完成，未强制重跑: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		if doc.is_running_align2tex2docx:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		# 🔧 使用数据库事务确保状态一致性
		frappe.db.begin()
		try:
			init_task_fields(doc, "align2tex2docx", "A2T2D", logger)
			doc.save(ignore_permissions=True, ignore_version=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			raise

		# 🔧 使用更具体的队列名称和jobname
		job = enqueue(
			"patent_hub.api.call_align2tex2docx._job",
			queue="long",
			timeout=TIMEOUT,
			job_name=f"align2tex2docx_{docname}",  # 唯一的job名称
			docname=docname,
			user=frappe.session.user,
		)

		logger.info(f"[Align2Tex2Docx] 已入队: {docname}, job_id: {job.id}")
		return {"success": True, "message": "任务已提交执行队列", "job_id": job.id}

	except frappe.PermissionError:
		return {"success": False, "error": "权限不足"}
	except Exception as e:
		logger.error(f"[Align2Tex2Docx] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e!s}"}


async def call_chain_with_retry(url: str, payload: dict, max_retries: int = 5) -> dict[str, Any]:
	"""优化的带重试机制的API调用"""

	# 🔧 优化超时配置
	timeout = httpx.Timeout(
		connect=10.0,
		read=300.0,  # 5分钟读取超时
		write=30.0,
		pool=30.0,
	)

	limits = httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0)

	# 🔧 指数退避策略
	backoff_factor = 2

	for attempt in range(max_retries):
		try:
			async with httpx.AsyncClient(
				timeout=timeout,
				limits=limits,
				http2=False,
				# 🔧 添加重试相关的headers
				headers={
					"User-Agent": "PatentHub/1.0",
					"Accept": "application/json",
					"Content-Type": "application/json",
				},
			) as client:
				logger.info(f"API调用尝试 {attempt + 1}/{max_retries}")
				response = await client.post(url, json=payload)

				if response.status_code == 200:
					result = response.json()
					logger.info(f"API调用成功，响应大小: {len(response.content)} 字节")
					return result

				# 🔧 区分不同的HTTP错误
				elif response.status_code >= 500:
					# 服务器错误，可以重试
					logger.warning(f"服务器错误 {response.status_code}，将重试")
					if attempt == max_retries - 1:
						raise httpx.HTTPStatusError(
							message=f"HTTP {response.status_code}: {response.text}",
							request=response.request,
							response=response,
						)
				else:
					# 客户端错误，不重试
					raise httpx.HTTPStatusError(
						message=f"HTTP {response.status_code}: {response.text}",
						request=response.request,
						response=response,
					)

		except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
			logger.warning(f"网络错误 (尝试 {attempt + 1}): {e}")
			if attempt == max_retries - 1:
				raise

		except httpx.HTTPStatusError as e:
			if e.response.status_code < 500:
				# 客户端错误不重试
				raise
			logger.warning(f"服务器错误 (尝试 {attempt + 1}): {e}")
			if attempt == max_retries - 1:
				raise

		# 🔧 指数退避
		if attempt < max_retries - 1:
			wait_time = backoff_factor**attempt
			logger.info(f"等待 {wait_time} 秒后重试...")
			await asyncio.sleep(wait_time)

	raise Exception("所有重试都失败了")


def _job(docname: str, user=None):
	"""优化的任务执行函数"""
	logger.info(f"[Align2Tex2Docx] 开始执行任务: {docname}")
	doc = None

	try:
		# 🔧 使用 frappe.get_doc 的 for_update 参数避免并发问题
		doc = frappe.get_doc("Patent Workflow", docname, for_update=True)

		# 防御性检查
		if not doc.is_running_align2tex2docx:
			logger.warning(f"[Align2Tex2Docx] 任务已非运行状态，跳过执行: {docname}")
			return

		# 🔧 使用 frappe.get_cached_doc 获取单例文档
		api_endpoint = frappe.get_cached_doc("API Endpoint", "API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.align2tex2docx.strip("/")
		url = f"{base_url}/{app_name}/invoke"

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.align2tex2docx_id)

		payload = {
			"input": {
				"patent_title": doc.patent_title,
				"base64file": text_to_base64(doc.application),
				"tmp_folder": tmp_folder,
			}
		}

		# 调用API
		result = asyncio.run(call_chain_with_retry(url, payload))

		# 🔧 重新获取文档确保数据最新
		doc.reload()

		# 再次检查任务状态
		if not doc.is_running_align2tex2docx:
			logger.warning(f"[Align2Tex2Docx] 任务在执行过程中被取消: {docname}")
			return

		# 解析响应
		output = result.get("output")
		if not output:
			raise ValueError("API响应格式错误：缺少output字段")

		if isinstance(output, str):
			output = json.loads(output)

		_res = universal_decompress(output.get("res", ""), as_json=True)

		# 🔧 批量更新字段，减少数据库操作
		update_fields = {
			"application_align": _res.get("application_align"),
			"application_tex": _res.get("application_tex"),
			"before_tex": _res.get("application_align"),
			"figure_codes": "\n==========\n".join([str(code) for code in _res.get("figure_codes", [])]),
		}

		# 处理DOCX文件
		application_docx_bytes = _res.get("application_docx_bytes")
		if application_docx_bytes:
			if isinstance(application_docx_bytes, dict):
				application_docx_bytes = restore_from_json_serializable(application_docx_bytes)

			if not isinstance(application_docx_bytes, bytes):
				raise ValueError(f"DOCX数据类型错误，期望bytes，实际: {type(application_docx_bytes)}")

			# 保存文件
			file_doc = save_docx_file(doc, application_docx_bytes)
			update_fields["application_docx_link"] = file_doc.name

		# 🔧 使用 frappe.db.set_value 批量更新
		for field, value in update_fields.items():
			if value is not None:
				doc.set(field, value)

		# 完成任务
		complete_task_fields(
			doc,
			"align2tex2docx",
			extra_fields={
				"time_s_align2tex2docx": output.get("TIME(s)", 0.0),
				"cost_align2tex2docx": output.get("cost", 0),
			},
		)

		# 🔧 使用 ignore_permissions 和 ignore_version 优化保存
		doc.save(ignore_permissions=True, ignore_version=True)
		frappe.db.commit()

		logger.info(f"[Align2Tex2Docx] 执行成功: {docname}")

		# 🔧 使用更具体的事件名称
		frappe.publish_realtime(
			"patent_workflow_update",
			{"docname": doc.name, "event": "align2tex2docx_done", "message": "Align2Tex2Docx 任务完成"},
			user=user,
		)

	except Exception as e:
		logger.error(f"[Align2Tex2Docx] 执行失败: {e}")
		logger.error(frappe.get_traceback())

		if doc:
			try:
				doc.reload()  # 重新加载以获取最新状态
				fail_task_fields(doc, "align2tex2docx", str(e))
				doc.save(ignore_permissions=True, ignore_version=True)
				frappe.db.commit()
			except Exception as save_error:
				logger.error(f"保存失败状态时出错: {save_error}")

			frappe.publish_realtime(
				"patent_workflow_update",
				{"docname": docname, "event": "align2tex2docx_failed", "error": str(e)},
				user=user,
			)


def save_docx_file(doc, docx_bytes):
	import re

	from frappe.utils.file_manager import save_file

	if not isinstance(docx_bytes, bytes):
		raise ValueError(f"参数必须是bytes类型，实际类型: {type(docx_bytes)}")

	base_filename = f"{doc.align2tex2docx_id}_application_"

	try:
		# 🔧 获取所有相关的文件
		all_files = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
			},
			fields=["name", "file_name", "file_url"],
		)
		logger.info(f"all_files: {all_files}")

		# 🔧 使用正则表达式精确匹配
		pattern = re.compile(rf"^{re.escape(doc.align2tex2docx_id)}.*\.docx$")
		files_to_delete = [f for f in all_files if f.file_name and pattern.match(f.file_name)]
		logger.info(f"找到需要删除的文件: {[f.file_name for f in files_to_delete]}")

		# 删除匹配的文件
		for file_to_delete in files_to_delete:
			try:
				frappe.delete_doc("File", file_to_delete.name, force=True, ignore_permissions=True)
				logger.info(f"删除旧文件: {file_to_delete.file_name}")
			except Exception as e:
				logger.warning(f"删除旧文件失败 {file_to_delete.name}: {e}")

		if files_to_delete:
			frappe.db.commit()
			# 等待一小段时间确保删除操作完成
			import time

			time.sleep(0.1)

	except Exception as e:
		logger.info(f"清理旧文件时出错: {e}")

	# 生成最终文件名
	final_filename = f"{base_filename}.docx"

	try:
		logger.info(f"保存文件 {final_filename}，大小: {len(docx_bytes)} 字节")

		file_doc = save_file(
			fname=final_filename, content=docx_bytes, dt=doc.doctype, dn=doc.name, is_private=1, decode=False
		)

		logger.info(f"文件保存成功: {file_doc.name}")
		return file_doc

	except Exception as e:
		logger.error(f"保存DOCX文件失败: {e}")
		raise
