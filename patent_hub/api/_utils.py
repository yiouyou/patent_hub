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
from typing import Any

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import now_datetime, time_diff_in_seconds

# 日志设置
logger = frappe.logger("app.patent_hub.patent_wf._util")
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


def get_compressed_base64_files(doc, table_field: str) -> list[dict]:
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
		# 压缩成 base64
		base64_str = compress_file_to_base64(file_path)
		results.append(
			{
				"file_path": file_path,
				"base64": base64_str,
				"original_filename": original_filename,
				"note": row.note,
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
# 🔹 重置超时未完成任务状态（通用函数）
# ---------------------------------------------------


def detect_and_reset_stuck_task(task_key: str, label: str, timeout_seconds=1800):
	"""
	通用函数：检测任务是否卡死（超过 timeout 秒未完成），并自动重置状态
	:param task_key: 任务字段前缀（如 align2tex2docx）
	:param label: 中文任务名称（用于日志和评论）
	:param timeout_seconds: 超时时间（秒）
	"""
	started_at_field = f"{task_key}_started_at"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	run_count_field = f"run_count_{task_key}"
	status_field = f"status_{task_key}"

	stuck_docs = frappe.get_all(
		"Patent Workflow",
		filters={is_running_field: 1, is_done_field: 0},
		fields=["name", started_at_field, run_count_field],
	)

	for doc in stuck_docs:
		if doc.get(run_count_field, 0) == 0:
			logger.debug(f"[{label}] 跳过未启动的任务: {doc.name}")
			continue

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


def detect_and_reset_all_stuck_tasks():
	"""
	批量检测所有任务，是否存在超时未完成的状态，并自动处理
	"""
	for key, label in TASKS:
		detect_and_reset_stuck_task(key, label)


# ---------------------------------------------------
# 🔹 task 相关工具
# ---------------------------------------------------


def init_task_fields(doc, task_key: str, prefix: str, logger=None):
	"""
	初始化任务状态字段，并生成 ID。
	- 设置为 Running 状态
	- 若首次运行，则生成 ID
	- 累加 run_count
	"""
	id_field = f"{task_key}_id"
	started_at_field = f"{task_key}_started_at"
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	run_count_field = f"run_count_{task_key}"

	# # 若尚未生成 ID，则生成，生成后不变
	# if not getattr(doc, id_field, None):
	# 	setattr(doc, id_field, generate_step_id(doc.patent_id, prefix))

	# 每次 init 都生成新的ID
	setattr(doc, id_field, generate_step_id(doc.patent_id, prefix))

	# 设置运行状态
	setattr(doc, is_running_field, 1)
	setattr(doc, is_done_field, 0)
	setattr(doc, status_field, "Running")
	setattr(doc, started_at_field, now_datetime())

	# 累加运行次数
	setattr(doc, run_count_field, getattr(doc, run_count_field, 0) + 1)

	if logger:
		logger.info(
			f"[{task_key}] 初始化任务: id={getattr(doc, id_field)}, status=Running, run_count={getattr(doc, run_count_field)}"
		)


def complete_task_fields(doc, task_key: str, extra_fields: dict = None):
	"""
	统一完成任务状态设置，并累加运行成功次数和累计耗时/成本。
	"""
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	success_count_field = f"success_count_{task_key}"

	setattr(doc, is_running_field, 0)
	setattr(doc, is_done_field, 1)
	setattr(doc, status_field, "Done")
	setattr(doc, success_count_field, getattr(doc, success_count_field, 0) + 1)

	if extra_fields:
		for key, value in extra_fields.items():
			setattr(doc, key, value)

			# 累计成本
			if key.startswith("cost_"):
				total_field = key.replace("cost_", "total_cost_")
				setattr(doc, total_field, getattr(doc, total_field, 0) + float(value or 0))

			# 累计时间
			if key.startswith("time_s_"):
				total_field = key.replace("time_s_", "total_time_s_")
				setattr(doc, total_field, getattr(doc, total_field, 0) + float(value or 0))

	doc.save()


def fail_task_fields(doc, task_key: str, error: str = None):
	"""
	设置任务失败状态，并记录错误信息（不增加 success_count）
	"""
	is_running_field = f"is_running_{task_key}"
	is_done_field = f"is_done_{task_key}"
	status_field = f"status_{task_key}"
	error_field = f"last_{task_key}_error"

	setattr(doc, is_running_field, 0)
	setattr(doc, is_done_field, 0)
	setattr(doc, status_field, "Failed")

	if hasattr(doc, error_field):
		setattr(doc, error_field, error or "运行失败")

	doc.save()


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
