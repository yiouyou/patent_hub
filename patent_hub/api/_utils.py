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
from typing import Any

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import now_datetime, time_diff_in_seconds

# 日志设置
logger = frappe.logger("app.patent_hub.patent_workflow._util")
logger.setLevel(logging.INFO)


# ---------------------------------------------------
# 🔹 文本压缩与解压（字符串 ⇄ base64）
# ---------------------------------------------------


def compress_str_to_base64(text: str) -> str:
	"""压缩字符串并转为 base64 编码"""
	compressed = gzip.compress(text.encode("utf-8"))
	return base64.b64encode(compressed).decode("utf-8")


def decompress_str_from_base64(base64_str: str) -> str:
	"""解压 base64 编码的压缩字符串"""
	compressed = base64.b64decode(base64_str.encode("utf-8"))
	return gzip.decompress(compressed).decode("utf-8")


# ---------------------------------------------------
# 🔹 JSON 对象压缩与解压（对象 ⇄ base64）
# ---------------------------------------------------


def compress_json_to_base64(obj: Any) -> str:
	"""将 Python 对象压缩并 base64 编码"""
	json_str = json.dumps(obj)
	return compress_str_to_base64(json_str)


def decompress_json_from_base64(base64_str: str) -> Any:
	"""解压 base64 字符串为 Python 对象"""
	json_str = decompress_str_from_base64(base64_str)
	return json.loads(json_str)


# ---------------------------------------------------
# 🔹 文件压缩与解压（文件 ⇄ base64）
# ---------------------------------------------------


def compress_file_to_base64(path: str) -> str:
	"""读取文件，压缩并转为 base64 字符串"""
	with open(path, "rb") as f:
		data = f.read()
	compressed = gzip.compress(data)
	return base64.b64encode(compressed).decode("utf-8")


def decompress_file_from_base64(base64_str: str, save_path: str):
	"""将 base64 压缩数据解压并保存为文件"""
	compressed = base64.b64decode(base64_str.encode("utf-8"))
	data = gzip.decompress(compressed)
	with open(save_path, "wb") as f:
		f.write(data)


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
# 🔹 重置超时未完成任务状态（通用函数）
# ---------------------------------------------------


def reset_stuck_task(task_key: str, label: str, timeout_seconds=1800):
	"""
	通用函数：检测任务是否卡死（超过 timeout 秒未完成），并自动重置状态
	:param task_key: 任务字段前缀（如 align2tex2docx）
	:param label: 中文任务名称（用于日志和评论）
	:param timeout_seconds: 超时时间（秒）
	"""
	started_at_field = f"{task_key}_started_at"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"

	stuck_docs = frappe.get_all(
		"Patent Workflow", filters={is_running_field: 1, is_done_field: 0}, fields=["name", started_at_field]
	)

	for doc in stuck_docs:
		started_at = doc.get(started_at_field)
		if not started_at:
			continue
		delta = time_diff_in_seconds(now_datetime(), started_at)
		if delta > timeout_seconds:
			_doc = frappe.get_doc("Patent Workflow", doc.name)
			setattr(_doc, is_running_field, 0)
			setattr(_doc, status_field, "Failed")
			_doc.append(
				"comments",
				{
					"comment_type": "Comment",
					"content": f"⚠️ 自动检测：{label} 运行超时（{delta}s），状态已重置为 Failed",
				},
			)
			_doc.save()
			logger.warning(f"[{label}] 任务超时自动重置: {_doc.name}")


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


def reset_all_stuck_tasks():
	"""
	批量检测所有任务，是否存在超时未完成的状态，并自动处理
	"""
	for key, label in TASKS:
		reset_stuck_task(key, label)


# ---------------------------------------------------
# 🔹 通用工具：任务状态字段管理（初始化、完成、失败等）
# ---------------------------------------------------


def init_task_fields(doc, task_key: str, prefix: str, logger=None):
	"""初始化任务状态字段，并生成 ID"""
	id_field = f"{task_key}_id"
	if not getattr(doc, id_field, None):
		setattr(doc, id_field, generate_step_id(doc.patent_id, prefix))
	setattr(doc, f"is_running_{task_key}", 1)
	setattr(doc, f"is_done_{task_key}", 0)
	setattr(doc, f"status_{task_key}", "Running")
	setattr(doc, f"{task_key}_started_at", now_datetime())

	if logger:
		logger.info(f"[{task_key}] 初始化任务: id={getattr(doc, id_field)}, status=Running")


def complete_task_fields(doc, task_key: str, extra_fields: dict = None):
	"""统一完成任务状态设置：Running → Done"""
	setattr(doc, f"is_running_{task_key}", 0)
	setattr(doc, f"is_done_{task_key}", 1)
	setattr(doc, f"status_{task_key}", "Done")

	if extra_fields:
		for key, value in extra_fields.items():
			setattr(doc, key, value)

	doc.save()


def fail_task_fields(doc, task_key: str, error: str = None):
	"""统一失败任务状态设置：Running → Failed"""
	setattr(doc, f"is_running_{task_key}", 0)
	setattr(doc, f"is_done_{task_key}", 0)
	setattr(doc, f"status_{task_key}", "Failed")

	error_field = f"last_{task_key}_error"
	if hasattr(doc, error_field):
		setattr(doc, error_field, error or "运行失败")

	doc.save()


@frappe.whitelist()
def reset_task_status(docname: str, task_key: str):
	"""手动重置任务状态（由用户调用）"""
	doc = frappe.get_doc("Patent Workflow", docname)
	fail_task_fields(doc, task_key, error="用户手动重置任务状态")
	frappe.db.commit()
	return {"success": True, "message": f"任务 {task_key} 状态已重置"}
