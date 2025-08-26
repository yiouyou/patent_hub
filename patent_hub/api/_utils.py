import base64
import gzip
import json
import logging
import os
import pickle
from collections.abc import Callable
from typing import Any, Optional

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import now_datetime, time_diff_in_seconds

# 日志设置
logger = frappe.logger("app.patent_hub.patent_wf._util")
# logger.setLevel(logging.DEBUG)


# ---------------------------------------------------
# 🔹 文本压缩与解压（字符串 ⇄ base64）
# ---------------------------------------------------


def make_json_serializable(obj: Any) -> Any:
	"""
	将任意对象转换为JSON可序列化的格式
	"""
	if obj is None or isinstance(obj, (str, int, float, bool)):
		return obj
	elif isinstance(obj, bytes):
		# bytes 转为特殊标记的字典
		return {"__type__": "bytes", "__data__": base64.b64encode(obj).decode("ascii")}
	elif isinstance(obj, dict):
		return {str(k): make_json_serializable(v) for k, v in obj.items()}
	elif isinstance(obj, (list, tuple)):
		result = [make_json_serializable(item) for item in obj]
		if isinstance(obj, tuple):
			# 保留 tuple 类型信息
			return {"__type__": "tuple", "__data__": result}
		return result
	elif hasattr(obj, "__dict__"):
		# 处理自定义对象
		return {
			"__type__": "object",
			"__class__": obj.__class__.__name__,
			"__data__": make_json_serializable(obj.__dict__),
		}
	else:
		# 其他类型转为字符串
		return {"__type__": "str_repr", "__data__": str(obj)}


def restore_from_json_serializable(obj: Any) -> Any:
	"""
	还原 JSON 序列化时转换的特殊类型
	"""
	if isinstance(obj, dict):
		if "__type__" in obj:
			if obj["__type__"] == "bytes":
				return base64.b64decode(obj["__data__"].encode("ascii"))
			elif obj["__type__"] == "tuple":
				return tuple(restore_from_json_serializable(item) for item in obj["__data__"])
			elif obj["__type__"] == "str_repr":
				return obj["__data__"]
			elif obj["__type__"] == "object":
				# 简单返回数据部分，不重建对象
				return restore_from_json_serializable(obj["__data__"])
		else:
			return {k: restore_from_json_serializable(v) for k, v in obj.items()}
	elif isinstance(obj, list):
		return [restore_from_json_serializable(item) for item in obj]
	else:
		return obj


def universal_compress(data: Any) -> str:
	"""
	通用压缩函数
	数据流: 任意数据 → 字节 → gzip压缩 → base64编码 → 字符串
	支持混合类型的字典和列表
	"""
	# 步骤1: 转为字节
	if isinstance(data, bytes):
		raw_bytes = data
	elif isinstance(data, str):
		raw_bytes = data.encode("utf-8")
	elif isinstance(data, (dict, list, tuple, int, float, bool)) or data is None:
		try:
			converted_data = make_json_serializable(data)
			json_str = json.dumps(converted_data, ensure_ascii=False, separators=(",", ":"))
			raw_bytes = json_str.encode("utf-8")
		except (TypeError, ValueError):
			# 如果 JSON 序列化仍然失败，使用 pickle 作为后备方案
			raw_bytes = pickle.dumps(data)
	else:
		# 对于其他复杂类型，直接使用 pickle
		raw_bytes = pickle.dumps(data)
	# 步骤2: gzip压缩
	compressed = gzip.compress(raw_bytes)
	# 步骤3: base64编码
	return base64.b64encode(compressed).decode("ascii")


def universal_decompress(compressed_str: str, as_json: bool = False) -> Any:
	"""
	通用解压缩函数
	"""
	try:
		# 步骤1: base64解码
		compressed_bytes = base64.b64decode(compressed_str.encode("ascii"))
		# 步骤2: gzip解压缩
		raw_bytes = gzip.decompress(compressed_bytes)
		if as_json:
			# 尝试 JSON 解析
			try:
				json_str = raw_bytes.decode("utf-8")
				data = json.loads(json_str)
				# 还原特殊类型
				return restore_from_json_serializable(data)
			except (json.JSONDecodeError, UnicodeDecodeError):
				# JSON 解析失败，使用 pickle
				return pickle.loads(raw_bytes)
		else:
			# 尝试字符串解码
			try:
				return raw_bytes.decode("utf-8")
			except UnicodeDecodeError:
				# 不是文本，使用 pickle
				return pickle.loads(raw_bytes)
	except Exception as e:
		raise ValueError(f"解压缩失败: {e}")


def text_to_base64(text: str) -> str:
	"""文本字符串转base64"""
	return base64.b64encode(text.encode("utf-8")).decode("ascii")


def get_attached_files(doc, table_field: str) -> list[dict]:
	"""
	从指定子表字段中读取 file 字段，转换为 base64 压缩字符串。
	返回格式：[{ file_path: ..., base64: ..., original_filename: ..., note: ... }, ...]
	"""
	results = []
	table = getattr(doc, table_field, [])
	for row in table:
		file_url = row.file
		if not file_url:
			continue
		# 判断路径位置（private/public）
		if file_url.startswith("/private/files/"):
			filename = file_url.replace("/private/files/", "")
			file_path = os.path.join(frappe.get_site_path("private", "files"), filename)
		elif file_url.startswith("/files/"):
			filename = file_url.replace("/files/", "")
			file_path = os.path.join(frappe.get_site_path("public", "files"), filename)
		else:
			frappe.throw(f"未知文件路径格式: {file_url}")
		if not os.path.exists(file_path):
			frappe.throw(f"文件不存在: {file_path}")
		# 获取原始文件名（包含扩展名）
		original_filename = os.path.basename(filename)
		with open(file_path, "rb") as f:
			file_data = f.read()
		results.append({"content_bytes": file_data, "original_filename": original_filename})
	return results


# ---------------------------------------------------
# 🔹 生成步骤唯一 ID（基于 patent_id 和前缀）
# ---------------------------------------------------


def generate_step_id(patent_id: str, prefix: str) -> str:
	"""
	使用 Frappe 的 make_autoname 生成：
	格式：{patent_id}-{prefix}-001
	"""
	return make_autoname(f"{patent_id}-{prefix}-.#")


# ---------------------------------------------------
# 🔹 心跳机制与统一字段
# ---------------------------------------------------

# 超时时间（秒）
TASK_TIMEOUTS = {
	"title2scene": 300,
	"info2tech": 300,
	"scene2tech": 300,
	"tech2application": 300,
	"review2revise": 300,
	"align2tex2docx": 300,
	"code2png": 300,
	"md2docx": 300,
}

# 建议心跳间隔
HEARTBEAT_INTERVAL = 100


def _resolve(doctype_or_doc, name: str | None = None):
	if hasattr(doctype_or_doc, "doctype"):
		return doctype_or_doc.doctype, doctype_or_doc.name
	return doctype_or_doc, name


def update_task_heartbeat(doctype_or_doc, task_key: str, name: str | None = None):
	"""
	⚠️ 无线程写库。始终在任务（队列）上下文中调用。
	"""
	doctype, docname = _resolve(doctype_or_doc, name)
	heartbeat_field = f"{task_key}_last_heartbeat"
	ts = now_datetime()
	try:
		frappe.db.set_value(doctype, docname, heartbeat_field, ts, update_modified=False)
		frappe.db.commit()
		logger.debug(f"[{task_key}] 心跳更新: {doctype}.{docname} at {ts}")
	except Exception as e:
		logger.error(f"[{task_key}] 心跳写入失败: {doctype}.{docname}, 错误: {e}")
		# 不中断主任务


def detect_and_reset_stuck_task(task_key: str, label: str, doctype: str, timeout_seconds=None):
	"""
	基于心跳的卡死检测，重置为 Failed，并实时推送 _failed。
	"""
	if timeout_seconds is None:
		timeout_seconds = TASK_TIMEOUTS.get(task_key, 300)  # 默认5分钟

	started_at_field = f"{task_key}_started_at"
	heartbeat_field = f"{task_key}_last_heartbeat"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	run_count_field = f"run_count_{task_key}"
	status_field = f"status_{task_key}"

	stuck_docs = frappe.get_all(
		doctype,
		filters={is_running_field: 1, is_done_field: 0},
		fields=["name", started_at_field, heartbeat_field, run_count_field],
	)

	for doc in stuck_docs:
		if doc.get(run_count_field, 0) == 0:
			logger.info(f"[{label}] 跳过未启动的任务: {doctype}.{doc.name}")
			continue

		check_time = doc.get(heartbeat_field) or doc.get(started_at_field)
		if not check_time:
			logger.warning(f"[{label}] 任务缺少时间戳: {doctype}.{doc.name}")
			continue

		delta = time_diff_in_seconds(now_datetime(), check_time)
		if delta > timeout_seconds:
			_doc = frappe.get_doc(doctype, doc.name)
			setattr(_doc, is_running_field, 0)
			setattr(_doc, status_field, "Failed")

			timeout_type = "心跳" if doc.get(heartbeat_field) else "启动"
			_doc.append(
				"comments",
				{
					"comment_type": "Comment",
					"content": f"⚠️ 自动检测：{label} {timeout_type}超时（{delta}s > {timeout_seconds}s），任务可能已卡死，状态已重置为 Failed。建议心跳间隔: {HEARTBEAT_INTERVAL}s",
				},
			)
			_doc.save()
			frappe.db.commit()
			# ✅ 实时广播失败（带房间 + after_commit）
			try:
				frappe.publish_realtime(
					event=f"{task_key}_failed",
					message={
						"docname": _doc.name,
						"doctype": doctype,
						"error": f"{label}{timeout_type}超时",
						"step": task_key,
					},
					doctype=doctype,
					docname=_doc.name,
					after_commit=True,
				)
			except Exception as e:
				logger.error(f"[{label}] publish_realtime 失败: {e}")
			logger.warning(
				f"[{label}] 任务{timeout_type}超时自动重置: {doctype}.{_doc.name}, 超时: {delta}s > {timeout_seconds}s"
			)


# 按 DocType 分组的任务配置
DOCTYPE_TASKS = {
	"Patent Workflow": [
		("title2scene", "Title2Scene"),
		("info2tech", "Info2Tech"),
		("scene2tech", "Scene2Tech"),
		("tech2application", "Tech2Application"),
		("review2revise", "Review2Revise"),
		("align2tex2docx", "Align2Tex2Docx"),
	],
	"Code2png": [
		("code2png", "Code2png"),
	],
	"Md2docx": [
		("md2docx", "Md2docx"),
	],
}


def detect_and_reset_all_stuck_tasks(doctype: str):
	if doctype not in DOCTYPE_TASKS:
		logger.warning(f"未找到 DocType '{doctype}' 的任务配置")
		return
	tasks = DOCTYPE_TASKS[doctype]
	logger.info(f"开始检测卡死任务：{doctype}（共 {len(tasks)} 个）...")
	for key, label in tasks:
		detect_and_reset_stuck_task(key, label, doctype)
	logger.info(f"卡死任务检测完成: {doctype}")


def detect_and_reset_all_stuck_tasks_multi():
	for doctype in DOCTYPE_TASKS.keys():
		try:
			detect_and_reset_all_stuck_tasks(doctype)
		except Exception as e:
			logger.error(f"检测 {doctype} 卡死任务失败: {e}")


# ---------------------------------------------------
# 🔹 任务字段（初始化/完成/失败）
# ---------------------------------------------------


def init_task_fields(doc, task_key: str, prefix: str, logger=logger):
	"""
	初始化任务状态字段，并生成 ID。
	- 设置为 Running 状态
	- 若首次运行，则生成 ID
	- 累加 run_count
	- 初始化心跳时间

	:param doc: 文档对象（任意DocType）
	:param task_key: 任务键名
	:param prefix: ID前缀
	:param logger: 日志对象
	"""
	id_field = f"{task_key}_id"
	started_at_field = f"{task_key}_started_at"
	heartbeat_field = f"{task_key}_last_heartbeat"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	run_count_field = f"run_count_{task_key}"

	if hasattr(doc, "patent_id"):
		setattr(doc, id_field, generate_step_id(doc.patent_id, prefix))
	else:
		setattr(doc, id_field, generate_step_id(doc.name, prefix))

	current_time = now_datetime()
	setattr(doc, is_running_field, 1)
	setattr(doc, is_done_field, 0)
	setattr(doc, status_field, "Running")
	setattr(doc, started_at_field, current_time)
	setattr(doc, heartbeat_field, current_time)
	setattr(doc, run_count_field, getattr(doc, run_count_field, 0) + 1)

	heartbeat_timeout = TASK_TIMEOUTS.get(task_key, 300)
	logger.info(
		f"[{task_key}] 初始化任务: {doc.doctype}.{doc.name}, id={getattr(doc, id_field)}, status=Running, "
		f"run_count={getattr(doc, run_count_field)}, 心跳超时={heartbeat_timeout}s, 建议心跳间隔={HEARTBEAT_INTERVAL}s"
	)


def complete_task_fields(
	doc, task_key: str, extra_fields: dict = None, logger=logger, push_realtime: bool = True
):
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	error_field = f"last_{task_key}_error"
	heartbeat_field = f"{task_key}_last_heartbeat"

	setattr(doc, is_running_field, 0)
	setattr(doc, is_done_field, 1)
	setattr(doc, status_field, "Done")
	setattr(doc, error_field, "成功！")
	setattr(doc, heartbeat_field, now_datetime())

	success_count_field = f"success_count_{task_key}"
	_success_count = int(getattr(doc, success_count_field, 0) or 0)
	setattr(doc, success_count_field, _success_count + 1)

	if extra_fields:
		for key, value in extra_fields.items():
			setattr(doc, key, value)
			if key.startswith("cost_"):
				total_field = key.replace("cost_", "total_cost_")
				try:
					current_total = float(getattr(doc, total_field, 0) or 0)
					new_value = float(value or 0)
					setattr(doc, total_field, current_total + new_value)
				except (ValueError, TypeError) as e:
					logger.info(f"Error converting cost values: {e}")
					setattr(doc, total_field, float(value or 0))
			if key.startswith("time_s_"):
				total_field = key.replace("time_s_", "total_time_s_")
				try:
					current_total = float(getattr(doc, total_field, 0) or 0)
					new_value = float(value or 0)
					setattr(doc, total_field, current_total + new_value)
				except (ValueError, TypeError) as e:
					logger.info(f"Error converting time values: {e}")
					setattr(doc, total_field, float(value or 0))

	doc.save()
	frappe.db.commit()
	logger.info(
		f"[{task_key}] 任务完成: {doc.doctype}.{doc.name}, status=Done, success_count={getattr(doc, success_count_field)}"
	)

	if push_realtime:
		try:
			frappe.publish_realtime(
				event=f"{task_key}_done",
				message={"docname": doc.name, "doctype": doc.doctype, "step": task_key},
				doctype=doc.doctype,
				docname=doc.name,
				after_commit=True,
			)
		except Exception as e:
			logger.error(f"[{task_key}] publish_realtime(_done) 失败: {e}")


def fail_task_fields(doc, task_key: str, error: str = None, logger=logger, push_realtime: bool = True):
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	error_field = f"last_{task_key}_error"
	heartbeat_field = f"{task_key}_last_heartbeat"

	setattr(doc, is_running_field, 0)
	setattr(doc, is_done_field, 0)
	setattr(doc, status_field, "Failed")
	setattr(doc, heartbeat_field, now_datetime())

	error_msg = error or "运行失败"
	if hasattr(doc, error_field):
		setattr(doc, error_field, error_msg)

	doc.save()
	frappe.db.commit()
	logger.error(f"[{task_key}] 任务失败: {doc.doctype}.{doc.name}, error={error_msg}")

	if push_realtime:
		try:
			frappe.publish_realtime(
				event=f"{task_key}_failed",
				message={"docname": doc.name, "doctype": doc.doctype, "error": error_msg, "step": task_key},
				doctype=doc.doctype,
				docname=doc.name,
				after_commit=True,
			)
		except Exception as e:
			logger.error(f"[{task_key}] publish_realtime(_failed) 失败: {e}")


@frappe.whitelist()
def cancel_task(docname: str, task_key: str, doctype: str):
	doc = frappe.get_doc(doctype, docname)
	is_running_field = f"is_running_{task_key}"
	if getattr(doc, is_running_field, 0) != 1:
		return {"success": False, "message": "任务未处于运行状态，无法取消"}

	fail_task_fields(doc, task_key, "任务被用户强制终止")
	frappe.db.commit()

	# 广播实时失败事件（容错再次发送；带房间 + after_commit）
	try:
		frappe.publish_realtime(
			event=f"{task_key}_failed",
			message={"docname": docname, "doctype": doctype, "error": "任务被用户强制终止", "step": task_key},
			doctype=doctype,
			docname=docname,
			after_commit=True,
		)
	except Exception as e:
		logger.error(f"[{task_key}] publish_realtime(cancel) 失败: {e}")

	return {"success": True, "message": f"{task_key} 已被终止"}


# ---------------------------------------------------
# 🔹 队列化工具（强烈建议 whitelisted API 使用）
# ---------------------------------------------------


def enqueue_long_task(
	*,
	doctype: str,
	docname: str,
	task_key: str,
	prefix: str,
	job_method: str | Callable,
	queue: str = "long",
	timeout: int = 3600,
	job_kwargs: dict | None = None,
) -> dict:
	"""
	在 whitelist 函数内调用：
	    doc = frappe.get_doc(doctype, docname)
	    init_task_fields(doc, task_key, prefix)
	    doc.save(); frappe.db.commit()
	    return enqueue_long_task(...)

	job_method：可传 import path 字符串或可调用对象（真正的长逻辑）。
	"""
	job_kwargs = job_kwargs or {}
	doc = frappe.get_doc(doctype, docname)
	step_id = getattr(doc, f"{task_key}_id")

	frappe.enqueue(
		job_method,
		queue=queue,
		timeout=timeout,
		job_name=step_id,
		doctype=doctype,
		docname=docname,
		task_key=task_key,
		**job_kwargs,
	)
	logger.info(
		f"[{task_key}] 已入队: {doctype}.{docname} -> job={step_id}, queue={queue}, timeout={timeout}s"
	)
	return {"ok": True, "queued": True, "job_name": step_id}


# ---------------------------------------------------
# 🔹 兼容性保留：with_heartbeat（不再起线程）
# ---------------------------------------------------


def with_heartbeat(task_key: str, doctype: str, heartbeat_interval: int = None):
	"""
	（兼容保留）不再起后台线程。仅做日志包装。
	建议：在队列任务中显式调用 update_task_heartbeat(doc, task_key)。
	"""

	def decorator(func):
		def wrapper(docname, *args, **kwargs):
			logger.warning(
				f"[{task_key}] with_heartbeat 装饰器已废弃线程实现，请改为队列任务中显式心跳调用。"
			)
			return func(docname, *args, **kwargs)

		return wrapper

	return decorator
