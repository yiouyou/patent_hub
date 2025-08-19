# ===============================================
# 📦 工具模块：patent_hub.patent_workflow._util
# 描述：提供通用任务工具函数，包括：
#  - 字符串压缩/解压
#  - JSON 压缩/解压
#  - 文件压缩/解压
#  - ID 生成
#  - 卡死任务检测与重置
#  - 通用任务状态重置
# ===============================================

import base64
import gzip
import json
import logging
import os
import pickle
from typing import Any

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import now_datetime, time_diff_in_seconds

# 日志设置
logger = frappe.logger("app.patent_hub.patent_wf._util")
logger.setLevel(logging.DEBUG)


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
				return obj["__data__"]  # 保持为字符串
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
		results.append(
			{
				"content_bytes": file_data,
				"original_filename": original_filename,
				# "file_path": file_path,
				# "note": row.note,
			}
		)
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
# 🔹 重置超时未完成任务状态（通用函数）- 优化版
# ---------------------------------------------------

# 不同任务的超时时间配置（秒）
TASK_TIMEOUTS = {
	"title2scene": 3600,  # 60分钟
	"info2tech": 3600,  # 60分钟
	"scene2tech": 3600,  # 60分钟
	"tech2application": 2400,  # 40分钟
	"review2revise": 1800,  # 30分钟
	"align2tex2docx": 1200,  # 20分钟
}


def update_task_heartbeat(doc, task_key: str):
	"""
	更新任务心跳时间，防止被误判为超时
	:param doc: Patent Workflow 文档对象
	:param task_key: 任务字段前缀
	"""
	heartbeat_field = f"{task_key}_last_heartbeat"
	setattr(doc, heartbeat_field, now_datetime())
	doc.save()
	logger.debug(f"[{task_key}] 更新心跳时间: {doc.name}")


def detect_and_reset_stuck_task(task_key: str, label: str, timeout_seconds=None):
	"""
	通用函数：检测任务是否卡死（超过 timeout 秒未完成），并自动重置状态
	支持心跳机制，优先检查心跳时间
	:param task_key: 任务字段前缀（如 align2tex2docx）
	:param label: 中文任务名称（用于日志和评论）
	:param timeout_seconds: 超时时间（秒），如果为None则使用TASK_TIMEOUTS中的配置
	"""
	# 使用配置的超时时间，如果没有配置则使用默认值
	if timeout_seconds is None:
		timeout_seconds = TASK_TIMEOUTS.get(task_key, 1800)

	started_at_field = f"{task_key}_started_at"
	heartbeat_field = f"{task_key}_last_heartbeat"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	run_count_field = f"run_count_{task_key}"
	status_field = f"status_{task_key}"

	stuck_docs = frappe.get_all(
		"Patent Workflow",
		filters={is_running_field: 1, is_done_field: 0},
		fields=["name", started_at_field, heartbeat_field, run_count_field],
	)

	for doc in stuck_docs:
		if doc.get(run_count_field, 0) == 0:
			logger.info(f"[{label}] 跳过未启动的任务: {doc.name}")
			continue

		# 优先检查心跳时间，如果没有心跳则使用开始时间
		check_time = doc.get(heartbeat_field) or doc.get(started_at_field)
		if not check_time:
			logger.warning(f"[{label}] 任务缺少时间戳: {doc.name}")
			continue

		delta = time_diff_in_seconds(now_datetime(), check_time)
		if delta > timeout_seconds:
			_doc = frappe.get_doc("Patent Workflow", doc.name)
			setattr(_doc, is_running_field, 0)
			setattr(_doc, status_field, "Failed")

			# 区分是心跳超时还是启动超时
			timeout_type = "心跳" if doc.get(heartbeat_field) else "启动"

			_doc.append(
				"comments",
				{
					"comment_type": "Comment",
					"content": f"⚠️ 自动检测：{label} {timeout_type}超时（{delta}s > {timeout_seconds}s），状态已重置为 Failed",
				},
			)
			_doc.save()
			logger.warning(f"[{label}] 任务{timeout_type}超时自动重置: {_doc.name}, 超时时间: {delta}s")


# ---------------------------------------------------
# 🔹 批量任务字段映射（统一处理多个任务）
# ---------------------------------------------------

TASKS = [
	("title2scene", "Title2Scene"),
	("info2tech", "Info2Tech"),
	("scene2tech", "Scene2Tech"),
	("tech2application", "Tech2Application"),
	("review2revise", "Review2Revise"),
	("align2tex2docx", "Align2Tex2Docx"),
]


def detect_and_reset_all_stuck_tasks():
	"""
	批量检测所有任务，是否存在超时未完成的状态，并自动处理
	使用各任务配置的超时时间
	"""
	for key, label in TASKS:
		detect_and_reset_stuck_task(key, label)


# ---------------------------------------------------
# 🔹 task 相关工具 - 优化版
# ---------------------------------------------------


def init_task_fields(doc, task_key: str, prefix: str, logger=None):
	"""
	初始化任务状态字段，并生成 ID。
	- 设置为 Running 状态
	- 若首次运行，则生成 ID
	- 累加 run_count
	- 初始化心跳时间
	"""
	id_field = f"{task_key}_id"
	started_at_field = f"{task_key}_started_at"
	heartbeat_field = f"{task_key}_last_heartbeat"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	run_count_field = f"run_count_{task_key}"

	# 每次 init 都生成新的ID
	setattr(doc, id_field, generate_step_id(doc.patent_id, prefix))

	# 设置运行状态
	current_time = now_datetime()
	setattr(doc, is_running_field, 1)
	setattr(doc, is_done_field, 0)
	setattr(doc, status_field, "Running")
	setattr(doc, started_at_field, current_time)
	setattr(doc, heartbeat_field, current_time)  # 初始化心跳时间

	# 累加运行次数
	setattr(doc, run_count_field, getattr(doc, run_count_field, 0) + 1)

	if logger:
		logger.info(
			f"[{task_key}] 初始化任务: id={getattr(doc, id_field)}, status=Running, run_count={getattr(doc, run_count_field)}, timeout={TASK_TIMEOUTS.get(task_key, 1800)}s"
		)


def complete_task_fields(doc, task_key: str, extra_fields: dict = None, logger=None):
	"""
	统一完成任务状态设置，并累加运行成功次数和累计耗时/成本。
	"""
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	error_field = f"last_{task_key}_error"
	heartbeat_field = f"{task_key}_last_heartbeat"

	setattr(doc, is_running_field, 0)
	setattr(doc, is_done_field, 1)
	setattr(doc, status_field, "Done")
	setattr(doc, error_field, "成功！")
	setattr(doc, heartbeat_field, now_datetime())  # 最后更新心跳时间

	success_count_field = f"success_count_{task_key}"
	_success_count = int(getattr(doc, success_count_field, 0) or 0)
	setattr(doc, success_count_field, _success_count + 1)

	if extra_fields:
		for key, value in extra_fields.items():
			setattr(doc, key, value)
			# 累计成本
			if key.startswith("cost_"):
				total_field = key.replace("cost_", "total_cost_")
				try:
					current_total = float(getattr(doc, total_field, 0) or 0)
					new_value = float(value or 0)
					setattr(doc, total_field, current_total + new_value)
				except (ValueError, TypeError) as e:
					logger.info(f"Error converting cost values: {e}")
					setattr(doc, total_field, float(value or 0))
			# 累计时间
			if key.startswith("time_s_"):
				total_field = key.replace("time_s_", "total_time_s_")
				try:
					current_total = float(getattr(doc, total_field, 0) or 0)
					new_value = float(value or 0)
					setattr(doc, total_field, current_total + new_value)
				except (ValueError, TypeError) as e:
					if logger:
						logger.info(f"Error converting time values: {e}")
					setattr(doc, total_field, float(value or 0))

	doc.save()
	if logger:
		logger.info(f"[{task_key}] 任务完成: status=Done, success_count={getattr(doc, success_count_field)}")


def fail_task_fields(doc, task_key: str, error: str = None, logger=None):
	"""
	设置任务失败状态，并记录错误信息（不增加 success_count）
	"""
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	error_field = f"last_{task_key}_error"
	heartbeat_field = f"{task_key}_last_heartbeat"

	setattr(doc, is_running_field, 0)
	setattr(doc, is_done_field, 0)
	setattr(doc, status_field, "Failed")
	setattr(doc, heartbeat_field, now_datetime())  # 更新心跳时间

	error_msg = error or "运行失败"
	if hasattr(doc, error_field):
		setattr(doc, error_field, error_msg)

	doc.save()
	if logger:
		logger.error(f"[{task_key}] 任务失败: error={error_msg}")


@frappe.whitelist()
def reset_task_status(docname: str, task_key: str):
	"""
	手动重置任务状态（用于用户在界面点击重置按钮）
	- 将任务标记为 Failed
	- 写入错误字段说明是用户操作
	"""
	doc = frappe.get_doc("Patent Workflow", docname)
	fail_task_fields(doc, task_key, error="用户手动重置任务状态")
	frappe.db.commit()
	return {"success": True, "message": f"任务 {task_key} 状态已重置为 Failed"}


@frappe.whitelist()
def cancel_task(docname: str, task_key: str):
	"""
	用户强制终止任务（前端点击取消按钮触发）
	"""
	doc = frappe.get_doc("Patent Workflow", docname)
	is_running_field = f"is_running_{task_key}"
	if getattr(doc, is_running_field, 0) != 1:
		return {"success": False, "message": "任务未处于运行状态，无法取消"}

	fail_task_fields(doc, task_key, "任务被用户强制终止")
	frappe.db.commit()

	# 广播实时失败事件
	frappe.publish_realtime(f"{task_key}_failed", {"docname": docname, "error": "任务被用户强制终止"})

	return {"success": True, "message": f"{task_key} 已被终止"}


@frappe.whitelist()
def update_heartbeat(docname: str, task_key: str):
	"""
	手动更新任务心跳时间（供长时间运行的API调用）
	"""
	try:
		doc = frappe.get_doc("Patent Workflow", docname)
		is_running_field = f"is_running_{task_key}"

		# 只有运行中的任务才能更新心跳
		if getattr(doc, is_running_field, 0) != 1:
			return {"success": False, "message": "任务未处于运行状态"}

		update_task_heartbeat(doc, task_key)
		frappe.db.commit()

		return {"success": True, "message": f"任务 {task_key} 心跳已更新"}
	except Exception as e:
		logger.error(f"更新心跳失败: {e!s}")
		return {"success": False, "message": f"更新心跳失败: {e!s}"}
