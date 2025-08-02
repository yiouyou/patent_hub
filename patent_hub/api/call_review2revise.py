import asyncio
import base64
import json
import logging
import os
import re

import frappe
import httpx
from frappe import enqueue

from patent_hub.api._utils import (
	complete_task_fields,
	fail_task_fields,
	get_attached_files,
	init_task_fields,
	text_to_base64,
	universal_decompress,
)

logger = frappe.logger("app.patent_hub.patent_wf.call_review2revise")
logger.setLevel(logging.INFO)

TIMEOUT = 1800


@frappe.whitelist()
def run(docname: str, force: bool = False):
	try:
		logger.info(f"[Review2Revise] 准备启动任务: {docname}, force={force}")
		doc = frappe.get_doc("Patent Workflow", docname)
		if not doc:
			return {"success": False, "error": f"文档 {docname} 不存在"}

		if doc.is_done_review2revise and not force:
			logger.warning(f"[Review2Revise] 任务已完成，跳过执行: {docname}")
			return {"success": True, "message": "任务已完成，未重复执行"}

		if doc.is_running_review2revise:
			return {"success": False, "error": "任务正在运行中，请等待完成"}

		init_task_fields(doc, "review2revise", "R2R", logger)
		doc.save()
		frappe.db.commit()

		enqueue(
			"patent_hub.api.call_review2revise._job",
			queue="long",
			timeout=TIMEOUT,
			docname=docname,
			user=frappe.session.user,
		)

		logger.info(f"[Review2Revise] 已入队执行: {docname}")
		return {"success": True, "message": "任务已提交执行队列"}

	except Exception as e:
		logger.error(f"[Review2Revise] 启动任务失败: {e}")
		logger.error(frappe.get_traceback())
		return {"success": False, "error": f"启动任务失败: {e}"}


def _job(docname: str, user=None):
	logger.info(f"[Review2Revise] 开始执行任务: {docname}")
	doc = None

	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		# 🛡 防御性：如果任务已非运行状态，则跳过执行
		if not doc.is_running_review2revise:
			logger.warning(f"[Review2Revise] 任务状态已取消，跳过执行: {docname}")
			return

		api_endpoint = frappe.get_single("API Endpoint")
		if not api_endpoint:
			frappe.throw("未配置 API Endpoint")

		base_url = api_endpoint.server_ip_port.rstrip("/")
		app_name = api_endpoint.review2revise.strip("/")
		url = f"{base_url}/{app_name}/invoke"
		logger.info(f"[Review2Revise] 请求 URL: {url}")

		review_files = get_attached_files(doc, "table_upload_review2revise")
		if not review_files:
			frappe.throw("未上传任何审查意见 PDF 文件，无法继续执行")
		last_review_base64 = review_files[-1].get("content_bytes")
		if not last_review_base64:
			frappe.throw("最后一个审查意见文件的 base64 编码为空")

		tmp_folder = os.path.join(api_endpoint.get_password("server_work_dir"), doc.review2revise_id)

		payload = {
			"input": {
				"review_base64": base64.b64encode(last_review_base64).decode("ascii"),
				"claims_base64": text_to_base64(doc.application_tex),
				"tmp_folder": tmp_folder,
			}
		}

		async def call_chain():
			async with httpx.AsyncClient(timeout=TIMEOUT) as client:
				return await client.post(url, json=payload)

		res = asyncio.run(call_chain())
		res.raise_for_status()
		output = json.loads(res.json()["output"])
		_res = universal_decompress(output.get("res", ""), as_json=True)

		doc.reply_review = _res.get("reply_review_txt")
		doc.revised_application = _res.get("revised_application_txt")

		reply_review_docx_bytes = _res.get("reply_review_docx_bytes")
		revised_application_docx_bytes = _res.get("revised_application_docx_bytes")
		if reply_review_docx_bytes:
			reply_file_doc = save_docx_file(doc, reply_review_docx_bytes, "reply_review")
			doc.reply_review_docx_link = reply_file_doc.name
		if revised_application_docx_bytes:
			revised_file_doc = save_docx_file(doc, revised_application_docx_bytes, "revised_application")
			doc.revised_application_docx_link = revised_file_doc.name

		complete_task_fields(
			doc,
			"review2revise",
			extra_fields={
				"time_s_review2revise": output.get("TIME(s)", 0.0),
				"cost_review2revise": output.get("cost", 0),
			},
		)

		logger.info(f"[Review2Revise] 执行成功: {docname}")
		frappe.db.commit()
		frappe.publish_realtime("review2revise_done", {"docname": doc.name}, user=user)

	except Exception as e:
		logger.error(f"[Review2Revise] 执行失败: {e}")
		logger.error(frappe.get_traceback())

		if doc:
			fail_task_fields(doc, "review2revise", str(e))
			frappe.db.commit()
			frappe.publish_realtime("review2revise_failed", {"error": str(e), "docname": docname}, user=user)


def save_docx_file(doc, docx_bytes, file_type):
	"""保存 docx bytes 为 File 文档

	Args:
		doc: Patent Workflow 文档
		docx_bytes: docx 文件的字节数据
		file_type: 文件类型，"reply_review" 或 "revised_application"
	"""
	from frappe.utils.file_manager import save_file

	# 生成文件名
	filename = f"{doc.name}_{file_type}.docx"

	# 如果已存在同名文件，先删除
	existing_files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "file_name": filename},
	)
	for existing_file in existing_files:
		frappe.delete_doc("File", existing_file.name)

	# 保存新文件
	file_doc = save_file(
		fname=filename,
		content=docx_bytes,
		dt=doc.doctype,
		dn=doc.name,
		is_private=1,  # 设为私有文件
	)

	logger.info(f"[Review2Revise] 已保存文件: {filename}, File ID: {file_doc.name}")
	return file_doc


@frappe.whitelist()
def download_reply_review(docname: str):
	"""下载 reply_review.docx 文件"""
	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		if not doc.reply_review_docx_link:
			frappe.throw("回复审查意见 DOCX 文件不存在，请先运行 Review2Revise 任务")

		file_doc = frappe.get_doc("File", doc.reply_review_docx_link)

		if not file_doc:
			frappe.throw("文件记录不存在")

		return {"success": True, "file_url": file_doc.file_url, "file_name": file_doc.file_name}

	except Exception as e:
		logger.error(f"[Review2Revise] 下载回复审查意见文件失败: {e}")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def download_revised_application(docname: str):
	"""下载 revised_application.docx 文件"""
	try:
		doc = frappe.get_doc("Patent Workflow", docname)

		if not doc.revised_application_docx_link:
			frappe.throw("修改后申请书 DOCX 文件不存在，请先运行 Review2Revise 任务")

		file_doc = frappe.get_doc("File", doc.revised_application_docx_link)

		if not file_doc:
			frappe.throw("文件记录不存在")

		return {"success": True, "file_url": file_doc.file_url, "file_name": file_doc.file_name}

	except Exception as e:
		logger.error(f"[Review2Revise] 下载修改后申请书文件失败: {e}")
		return {"success": False, "error": str(e)}
