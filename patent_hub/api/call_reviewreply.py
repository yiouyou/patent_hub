import asyncio
import base64
import contextlib
import json
import os
import re
import time
from contextlib import contextmanager
from typing import Any

import frappe
import httpx
from frappe.utils.file_manager import save_file

from patent_hub.api._utils import (
	complete_task_fields,
	enqueue_long_task,
	fail_task_fields,
	get_attached_files,
	init_task_fields,
	restore_from_json_serializable,
	universal_decompress,
	update_task_heartbeat,
)

# 日志
logger = frappe.logger("app.patent_hub.review_reply.call_reviewreply")
# logger.setLevel(logging.DEBUG)

# 队列任务最大执行时长（秒）
TIMEOUT = 4000

HTTP_CONFIG = {
	"timeout": httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0),
	"limits": httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0),
	"headers": {
		"User-Agent": "PatentHub/1.0",
		"Accept": "application/json",
		"Content-Type": "application/json",
	},
}

TASK_KEY = "reviewreply"
TASK_LABEL = "ReviewReply"
DOCTYPE = "Review Reply"
STEP_PREFIX = "RRP"


@contextmanager
def atomic_transaction():
	"""短事务：仅包裹状态写入/回写，避免长事务"""
	try:
		frappe.db.begin()
		yield
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise


# -------------------------------
# Public API（whitelisted）：只入队
# -------------------------------
@frappe.whitelist()
def run(docname: str, force: bool = False):
	"""
	启动 ReviewReply：初始化任务字段 + 入队，立刻返回
	返回：{ ok: True, queued: True, job_name: ... }
	"""
	try:
		doc = frappe.get_doc(DOCTYPE, docname)
		if not doc:
			return {"ok": False, "error": f"文档 {docname} 不存在"}
		doc.check_permission("write")

		# 前置必填校验（避免空跑）
		err = _validate_required_fields(doc)
		if err:
			return {"ok": False, "error": err}

		# 并发保护：短事务内检查与初始化
		with atomic_transaction():
			locked = frappe.db.sql(
				"""
                SELECT is_done_reviewreply AS done, is_running_reviewreply AS running
                FROM `tabReview Reply`
                WHERE name=%s FOR UPDATE
                """,
				(docname,),
				as_dict=True,
			)
			if not locked:
				return {"ok": False, "error": f"文档 {docname} 不存在"}

			if locked[0].running:
				return {"ok": False, "error": "任务正在运行中，请等待完成"}
			if locked[0].done and not force:
				return {"ok": False, "error": "任务已完成，未重复执行（传入 force=True 可重跑）"}

			# 初始化任务字段：置 Running、生成 step_id、起始心跳
			init_task_fields(doc, TASK_KEY, STEP_PREFIX)
			doc.save(ignore_permissions=True, ignore_version=True)

		# 入队（统一封装）
		return enqueue_long_task(
			doctype=DOCTYPE,
			docname=docname,
			task_key=TASK_KEY,
			prefix=STEP_PREFIX,
			job_method="patent_hub.api.call_reviewreply._job",
			queue="long",
			timeout=TIMEOUT,
			job_kwargs={"force": force},
		)

	except frappe.PermissionError:
		logger.warning(f"权限不足: {docname}, user: {frappe.session.user}")
		return {"ok": False, "error": "权限不足"}
	except Exception as e:
		logger.error(f"启动任务失败 [{docname}]: {e}")
		return {"ok": False, "error": f"启动任务失败: {e!s}"}


def _validate_required_fields(doc) -> str | None:
	"""验证必填字段"""
	if not isinstance(getattr(doc, "patent_title", None), str) or not doc.patent_title.strip():
		return "patent_title 字段不能为空"
	if not get_attached_files(doc, "table_upload_review"):
		return "请至少上传一个审查意见文件（table_upload_review）"
	if not get_attached_files(doc, "table_upload_pdoc"):
		return "请至少上传一个专利申请书文件（table_upload_pdoc）"
	return None


# -------------------------------
# 队列任务：真正执行逻辑
# enqueue_long_task 会以 _job(doctype, docname, task_key, **job_kwargs) 调用
# -------------------------------
def _job(doctype: str, docname: str, task_key: str, *, force: bool = False):
	"""
	在 RQ 队列中执行 ReviewReply：
	- 并发运行：远端 API 调用 + 协程心跳（无线程）
	- 成功：complete_task_fields（自动 realtime: reviewreply_done）
	- 失败：fail_task_fields（自动 realtime: reviewreply_failed）
	"""
	assert doctype == DOCTYPE and task_key == TASK_KEY, "任务入参不匹配"
	logger.info(f"[{TASK_LABEL}] 开始执行任务: {docname}, force={force}")

	try:
		# 确认仍处于运行态
		running = frappe.db.get_value(DOCTYPE, docname, f"is_running_{TASK_KEY}", as_dict=False)
		if not running:
			logger.warning(f"[{TASK_LABEL}] 任务已非运行状态，跳过执行: {docname}")
			return

		# 读取输入（避免长事务）
		doc = frappe.get_doc(DOCTYPE, docname)
		review_files = get_attached_files(doc, "table_upload_review")
		pdoc_files = get_attached_files(doc, "table_upload_pdoc")
		if not review_files:
			raise ValueError("未上传任何审查意见文件（table_upload_review 为空）")
		if not pdoc_files:
			raise ValueError("未上传任何专利申请书文件（table_upload_pdoc 为空）")

		last_review_bytes = review_files[-1].get("content_bytes")
		last_pdoc_bytes = pdoc_files[-1].get("content_bytes")
		if not last_review_bytes:
			raise ValueError("最后一个审查意见文件内容为空")
		if not last_pdoc_bytes:
			raise ValueError("最后一个专利申请书文件内容为空")

		# API 目标与 payload（不在事务中）
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			raise ValueError("未配置 API Endpoint")
		if not api_endpoint.review_reply:
			raise ValueError("API Endpoint.review_reply 未配置")

		url = f"{api_endpoint.server_ip_port.rstrip('/')}/{api_endpoint.review_reply.strip('/')}/invoke"

		step_id = frappe.db.get_value(DOCTYPE, docname, f"{TASK_KEY}_id")
		if not step_id:
			raise ValueError("未找到任务 step_id")
		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), step_id)

		payload = {
			"input": {
				"review_base64": base64.b64encode(last_review_bytes).decode("ascii"),
				"claims_base64": base64.b64encode(last_pdoc_bytes).decode("ascii"),
				"tmp_folder": tmp_folder,
			}
		}

		# 并发执行：远端调用 + 心跳
		result = asyncio.run(_run_api_with_heartbeat(url, payload, doctype, docname, task_key))

		# 处理结果并落库
		_process_api_result(docname, result)

	except Exception as e:
		logger.error(f"[{TASK_LABEL}] 执行失败 [{docname}]: {e}")
		_handle_task_failure(docname, str(e))
		raise


# -------------------------------
# 并发：API 调用 + 协程心跳
# -------------------------------
async def _run_api_with_heartbeat(url: str, payload: dict, doctype: str, docname: str, task_key: str):
	api_task = asyncio.create_task(call_chain_with_retry_async(url, payload))
	hb_task = asyncio.create_task(_heartbeat_loop(doctype, docname, task_key))

	done, pending = await asyncio.wait({api_task, hb_task}, return_when=asyncio.FIRST_COMPLETED)

	if api_task in done:
		try:
			result = api_task.result()
		finally:
			hb_task.cancel()
			with contextlib.suppress(asyncio.CancelledError):
				await hb_task
		return result

	api_task.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await api_task
	raise RuntimeError("心跳任务异常终止")


async def _heartbeat_loop(doctype: str, docname: str, task_key: str, interval: int = 100):
	try:
		while True:
			update_task_heartbeat(doctype, task_key, name=docname)
			await asyncio.sleep(interval)
	except asyncio.CancelledError:
		update_task_heartbeat(doctype, task_key, name=docname)
		raise


# -------------------------------
# HTTP 调用与重试（async 版）
# -------------------------------
async def call_chain_with_retry_async(url: str, payload: dict, max_retries: int = 5) -> dict[str, Any]:
	for attempt in range(max_retries):
		try:
			async with httpx.AsyncClient(**HTTP_CONFIG) as client:
				logger.info(f"API调用尝试 {attempt + 1}/{max_retries}")
				resp = await client.post(url, json=payload)

				if resp.status_code == 200:
					logger.info(f"API调用成功，响应大小: {len(resp.content)} 字节")
					return resp.json()

				if resp.status_code < 500:
					resp.raise_for_status()

				logger.warning(f"服务器错误 {resp.status_code}，将重试")
				if attempt == max_retries - 1:
					resp.raise_for_status()

		except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
			logger.warning(f"网络错误 (尝试 {attempt + 1}): {e}")
			if attempt == max_retries - 1:
				raise
		except httpx.HTTPStatusError as e:
			if e.response.status_code < 500:
				raise
			logger.warning(f"服务器错误 (尝试 {attempt + 1}): {e}")
			if attempt == max_retries - 1:
				raise

		# 指数退避
		if attempt < max_retries - 1:
			wait_time = 2**attempt
			logger.info(f"等待 {wait_time} 秒后重试...")
			await asyncio.sleep(wait_time)

	raise Exception("所有重试都失败了")


# -------------------------------
# 结果处理 / 成功落库
# -------------------------------
def _process_api_result(docname: str, result: dict, user: str | None = None):
	with atomic_transaction():
		doc = frappe.get_doc(DOCTYPE, docname)

		# 若执行途中被取消，直接退出
		running = getattr(doc, f"is_running_{TASK_KEY}", 0)
		if not running:
			logger.warning(f"[{TASK_LABEL}] 任务在执行过程中被取消: {docname}")
			return

		output = result.get("output")
		if not output:
			raise ValueError("API响应格式错误：缺少 output 字段")

		if isinstance(output, str):
			output = json.loads(output)

		res_data = universal_decompress(output.get("res", ""), as_json=True) or {}

		# 文本结果
		review_reply_txt = res_data.get("review_reply_txt")
		application_revised_txt = res_data.get("application_revised_txt")

		# DOCX 二进制
		review_reply_docx_bytes = res_data.get("review_reply_docx_bytes")
		application_revised_docx_bytes = res_data.get("application_revised_docx_bytes")

		if isinstance(review_reply_docx_bytes, dict):
			review_reply_docx_bytes = restore_from_json_serializable(review_reply_docx_bytes)
		if isinstance(application_revised_docx_bytes, dict):
			application_revised_docx_bytes = restore_from_json_serializable(application_revised_docx_bytes)

		# 清理旧 DOCX（按 step_id 前缀）
		_cleanup_old_docx_files(doc)

		update_fields = {}

		if isinstance(review_reply_docx_bytes, (bytes, bytearray)):
			file_doc = _save_docx_file(doc, bytes(review_reply_docx_bytes), "review_reply")
			update_fields["review_reply_docx_link"] = file_doc.name

		if isinstance(application_revised_docx_bytes, (bytes, bytearray)):
			file_doc = _save_docx_file(doc, bytes(application_revised_docx_bytes), "application_revised")
			update_fields["application_revised_docx_link"] = file_doc.name

		# 回填文本字段
		update_fields["review_reply_txt"] = review_reply_txt
		update_fields["application_revised_txt"] = application_revised_txt

		for field, value in update_fields.items():
			if value is not None:
				doc.set(field, value)

		# 统一完成（会自动 publish_realtime: reviewreply_done）
		complete_task_fields(
			doc,
			TASK_KEY,
			extra_fields={
				"time_s_reviewreply": output.get("TIME(s)", 0.0),
				"cost_reviewreply": output.get("cost", 0),
			},
		)


# -------------------------------
# 失败处理
# -------------------------------
def _handle_task_failure(docname: str, error_msg: str):
	try:
		with atomic_transaction():
			doc = frappe.get_doc(DOCTYPE, docname)
			fail_task_fields(doc, TASK_KEY, error_msg)
	except Exception as save_error:
		logger.error(f"[{TASK_LABEL}] 保存失败状态时出错: {save_error}")


# -------------------------------
# 文件保存 / 清理
# -------------------------------
def _save_docx_file(doc, docx_bytes: bytes, file_type: str):
	"""保存 DOCX 文件到 File DocType 并返回 File 文档"""
	if not isinstance(docx_bytes, (bytes, bytearray)):
		raise ValueError(f"参数必须是 bytes/bytearray，实际类型: {type(docx_bytes)}")
	filename = f"{getattr(doc, f'{TASK_KEY}_id')}_{file_type}_.docx"
	try:
		logger.info(f"保存文件 {filename}，大小: {len(docx_bytes)} 字节")
		file_doc = save_file(
			fname=filename,
			content=bytes(docx_bytes),
			dt=doc.doctype,
			dn=doc.name,
			is_private=1,
			decode=False,
		)
		logger.info(f"文件保存成功: {file_doc.name}")
		return file_doc
	except Exception as e:
		logger.error(f"保存 DOCX 文件失败: {e}")
		raise


def _cleanup_old_docx_files(doc):
	"""清理当前任务 ID 前缀相关的旧 DOCX 文件"""
	try:
		all_files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
			fields=["name", "file_name"],
		)
		id_prefix = getattr(doc, f"{TASK_KEY}_id", "")  # e.g. PAT-xxx-R2R-001
		if not id_prefix:
			return
		# 只清理同一批次（相同 step_id 前缀）的文件
		prefix = id_prefix.rsplit("-", 1)[0]
		pattern = re.compile(rf"^{re.escape(prefix)}.*\.docx$")
		files_to_delete = [f for f in all_files if f.get("file_name") and pattern.match(f["file_name"])]

		if not files_to_delete:
			return

		logger.info(f"找到需要删除的文件: {[f['file_name'] for f in files_to_delete]}")
		for file_info in files_to_delete:
			try:
				frappe.delete_doc("File", file_info.name, force=True, ignore_permissions=True)
				logger.info(f"删除旧文件: {file_info.file_name}")
			except Exception as e:
				logger.warning(f"删除旧文件失败 {file_info.name}: {e}")

		time.sleep(0.1)  # 轻量等待，确保删除完成
	except Exception as e:
		logger.warning(f"清理旧文件时出错: {e}")
