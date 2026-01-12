import asyncio
import contextlib
import json
import os
from contextlib import contextmanager
from typing import Any

import frappe
import httpx

from patent_hub.api._utils import (complete_task_fields, enqueue_long_task,
                                   fail_task_fields, init_task_fields,
                                   universal_compress, universal_decompress,
                                   update_task_heartbeat)

# 配置
logger = frappe.logger("app.patent_hub.patent_wf.call_title2scene")
# logger.setLevel(logging.DEBUG)

# 队列任务最大执行时长（秒）
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

TASK_KEY = "title2scene"
TASK_LABEL = "Title2Scene"
DOCTYPE = "Patent Workflow"
STEP_PREFIX = "T2S"


@contextmanager
def atomic_transaction():
	"""原子事务（小范围使用，避免长事务）"""
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
	启动 Title2Scene：只做状态初始化与入队，立即返回。
	返回结构与前端对齐：{ ok: True, queued: True, job_name: ... }
	"""
	try:
		doc = frappe.get_doc(DOCTYPE, docname)
		if not doc:
			return {"ok": False, "error": f"文档 {docname} 不存在"}
		doc.check_permission("write")

		# 并发保护：短事务内检查并初始化
		with atomic_transaction():
			# 再取一遍状态（行级锁）
			locked = frappe.db.sql(
				"""
                SELECT is_done_title2scene AS done, is_running_title2scene AS running
                FROM `tabPatent Workflow`
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

		# 入队（使用统一封装，返回 {ok, queued, job_name}）
		return enqueue_long_task(
			doctype=DOCTYPE,
			docname=docname,
			task_key=TASK_KEY,
			prefix=STEP_PREFIX,
			job_method="patent_hub.api.call_title2scene._job",  # 下面定义的真正任务
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


# -------------------------------
# 队列任务：真正执行逻辑
# enqueue_long_task 会以 _job(doctype, docname, task_key, **job_kwargs) 调用
# -------------------------------
def _job(doctype: str, docname: str, task_key: str, *, force: bool = False):
	"""
	在 RQ 队列中执行 Title2Scene：
	- 并发运行：远端 API 调用 + 周期心跳（async 协程，无线程）
	- 成功：complete_task_fields（自动 realtime: title2scene_done）
	- 失败：fail_task_fields（自动 realtime: title2scene_failed）
	"""
	assert doctype == DOCTYPE and task_key == TASK_KEY, "任务入参不匹配"
	logger.info(f"[{TASK_LABEL}] 开始执行任务: {docname}, force={force}")

	try:
		# 读取一次，确认仍处于运行态（避免误触）
		status = frappe.db.get_value(DOCTYPE, docname, f"is_running_{TASK_KEY}", as_dict=False)
		if not status:
			logger.warning(f"[{TASK_LABEL}] 任务已非运行状态，跳过执行: {docname}")
			return

		# 准备 API 目标与 payload（不在事务内）
		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			raise ValueError("未配置 API Endpoint")

		url = f"{api_endpoint.server_ip_port.rstrip('/')}/{api_endpoint.title2scene.strip('/')}/invoke"

		# 读取 step_id 以便拼接工作目录
		step_id = frappe.db.get_value(DOCTYPE, docname, f"{TASK_KEY}_id")
		if not step_id:
			raise ValueError("未找到任务 step_id")

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), step_id)

		# 读取必要字段
		title = frappe.db.get_value(DOCTYPE, docname, "title")
		patent_domain = frappe.db.get_value(DOCTYPE, docname, "patent_domain")

		# 中间文件
		doc = frappe.get_doc(DOCTYPE, docname)
		mid_files = _get_title2scene_mid_files(doc)

		payload = {
			"input": {
				"patent_title": title,
				"patent_domain": patent_domain,
				"tmp_folder": tmp_folder,
				"mid_files": universal_compress(mid_files),
			}
		}

		# 并发：远端调用 + 心跳
		result = asyncio.run(_run_api_with_heartbeat(url, payload, doctype, docname, task_key))

		# 处理结果并写回
		_process_api_result(docname, result)

	except Exception as e:
		logger.error(f"[{TASK_LABEL}] 执行失败 [{docname}]: {e}")
		_handle_task_failure(docname, str(e))
		raise


# -------------------------------
# 并发：API 调用 + 心跳
# -------------------------------
async def _run_api_with_heartbeat(url: str, payload: dict, doctype: str, docname: str, task_key: str):
	"""
	使用 asyncio 并发执行：
	  - call_chain_with_retry_async：远端调用（可能数百秒）
	  - heartbeat_loop：每 HEARTBEAT_INTERVAL 秒写一次 last_heartbeat
	"""
	api_task = asyncio.create_task(call_chain_with_retry_async(url, payload))
	hb_task = asyncio.create_task(_heartbeat_loop(doctype, docname, task_key))

	done, pending = await asyncio.wait({api_task, hb_task}, return_when=asyncio.FIRST_COMPLETED)

	# 若 API 先完成，停止心跳
	if api_task in done:
		try:
			result = api_task.result()
		finally:
			hb_task.cancel()
			with contextlib.suppress(asyncio.CancelledError):
				await hb_task
		return result

	# 若心跳异常结束（几乎不该发生），取消 API 并报错
	api_task.cancel()
	with contextlib.suppress(asyncio.CancelledError):
		await api_task
	raise RuntimeError("心跳任务异常终止")


async def _heartbeat_loop(doctype: str, docname: str, task_key: str, interval: int = 100):
	"""
	协程心跳：同一线程内周期写库；避免跨线程 DB。
	"""
	try:
		while True:
			update_task_heartbeat(doctype, task_key, name=docname)
			await asyncio.sleep(interval)
	except asyncio.CancelledError:
		# 退出前再写一次，尽量让外界看到“刚更新过”
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

				# 5xx 可重试，4xx 直接抛出
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

		# 字段映射
		field_mapping = {
			"scene_deepsearch": "scene_deepsearch",
			"scene": "scene",
		}

		# 批量回填
		for api_field, doc_field in field_mapping.items():
			if api_field in res_data:
				value = res_data.get(api_field)
				if value is not None:
					doc.set(doc_field, value)
		
		# 提供给下一步
		if res_data.get("scene_deepsearch"):
			doc.scene = res_data.get("scene_deepsearch")

		# 统一完成（会自动 publish_realtime: title2scene_done）
		complete_task_fields(
			doc,
			TASK_KEY,
			extra_fields={
				"time_s_title2scene": output.get("TIME(s)", 0.0),
				"cost_title2scene": output.get("cost", 0),
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
# 中间文件收集
# -------------------------------
def _get_title2scene_mid_files(doc) -> list[dict]:
	"""获取 title2scene 中间文件"""
	field_to_filename = {
		"scene_deepsearch": "scene_deepsearch.txt",
	}

	files = []
	for field, filename in field_to_filename.items():
		content = getattr(doc, field, "")
		if isinstance(content, str) and content.strip():
			files.append({"content": content, "original_filename": filename})

	logger.info(f"[{TASK_LABEL}] 找到 {len(files)} 个中间文件")
	return files
